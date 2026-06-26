"""
ortho_main.py — ORTHO 단독 봇 진입점 (가상매매) [TARGET: 15분봉]
════════════════════════════════════════════════════════════════════
GitHub Actions 15분 cron. SINGLE_SYMBOL 환경변수가 있으면 그 심볼만(매트릭스),
없으면 SYMBOLS 전체를 순회한다.

심볼별 흐름:
  1) 맥락 데이터 수집(ls/taker)        — ortho_data.collect_context
  2) ORTHO-3 평가 → 가상 신호 0~2건    — ortho_engine.evaluate
  3) Notion 가상기록 (Status=OPEN)      — ortho_notion.log_signal  (항상)
  4) 텔레그램 알림                       — ortho_notify.notify_signal (ALERT_ENABLED=true 일 때만)

실주문 없음. 채점은 ortho_resolver(별도 5분 cron)가 수행.
"""
import logging
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))
import ortho_config as oc
import ortho_data as od
import ortho_engine as engine
import ortho_notion as notion
import ortho_notify as notify
import timeutil


def setup_logging():
    logging.basicConfig(level=getattr(logging, oc.LOG_LEVEL, logging.INFO),
                        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    return logging.getLogger("ortho.main")


def collect_candidates(exchange, symbol: str, open_idx: dict, logger):
    """한 심볼 평가 → (라이브 후보, Shadow 후보). ① 이미-OPEN 중복(정적)만 여기서 거른다.
    슬롯·방향 캡(②③)은 admit 단계에서 R6 품질 정렬 후 적용한다.
    엔진이 차단점에서 만든 Shadow 후보(sig['shadow'])는 라이브 중복검사 우회 → 별도 반환."""
    try:
        context = od.collect_context(exchange, symbol)
        signals = engine.evaluate(exchange, symbol, context)
        if not signals:
            logger.info(f"[{symbol}] 신호 없음")
            return [], []
        live, shadow = [], []
        for sig in signals:
            if sig.get("shadow"):                 # 차단된 셋업(FN 후보) — 라이브 게이트 우회
                shadow.append(sig)
                continue
            sym = sig["symbol"]; dr = (sig.get("direction") or "").lower(); pol = sig["polarity"]
            # ① 동일 셋업이 이미 OPEN → 해소 전까지 재진입 금지(중복 적재 차단)
            if (sym, pol, dr) in open_idx["keys"]:
                logger.info(f"[{sym}] {pol} {dr.upper()} 이미 OPEN — 중복 스킵")
                continue
            live.append(sig)
        return live, shadow
    except Exception as e:
        logger.error(f"[{symbol}] 처리 오류: {e}\n{traceback.format_exc()}")
        return [], []


def make_shadow_ctx(open_idx: dict, logger):
    """Shadow 적재 컨텍스트: Shadow DB의 기존 OPEN 색인 + 런당 쿼터. 비활성 시 None."""
    if not oc.SHADOW_ENABLED:
        return None
    sidx = notion.open_index(database_id=oc.NOTION_SHADOW_DB_ID)
    logger.info(f"   🌑 Shadow 로깅 ON — 기존 OPEN shadow {len(sidx['keys'])}건 · 쿼터 {oc.SHADOW_MAX_PER_RUN}")
    return {"keys": sidx["keys"], "live_keys": open_idx["keys"], "quota": oc.SHADOW_MAX_PER_RUN}


def shadow_emit(sig: dict, reason: str, sctx, logger) -> bool:
    """차단된 셋업을 Shadow DB에 적재(FN 측정). 카테고리 게이트·중복·쿼터를 모두 통과해야 기록."""
    if sctx is None or not oc.SHADOW_ENABLED:
        return False
    cat = (reason or "").split(":")[0].upper()
    if cat not in oc.SHADOW_REASONS:
        return False
    sym = sig["symbol"]; dr = (sig.get("direction") or "").lower(); pol = sig["polarity"]
    key = (sym, pol, dr)
    if key in sctx["keys"]:                # 이미 OPEN shadow → 중복 적재 방지
        return False
    if key in sctx["live_keys"]:           # 같은 셋업이 라이브로 살아있음 → '놓침' 아님, 제외
        return False
    if sctx["quota"] <= 0:
        logger.info("   🌑 Shadow 쿼터 소진 — skip")
        return False
    pid = notion.log_signal(sig, database_id=oc.NOTION_SHADOW_DB_ID, status="OPEN", blocked_by=reason)
    if pid:
        sctx["keys"].add(key)
        sctx["quota"] -= 1
        return True
    return False


def admit(sig: dict, open_idx: dict, sctx, logger) -> bool:
    """슬롯·방향 캡(②③)을 적용하고 통과 시 기록·알림·색인갱신. R6에서 품질순으로 호출.
    캡으로 막힌 셋업은(=리스크한도 기회비용 FN) Shadow DB에 SLOT/DIRCAP로 적재."""
    sym = sig["symbol"]; dr = (sig.get("direction") or "").lower(); pol = sig["polarity"]
    # ② 방향별 동시 슬롯(MAX_POS_DIR) 초과 → 스킵 (심볼·방향 단위)
    if open_idx["dir_count"].get((sym, dr), 0) >= oc.MAX_POS_DIR:
        logger.info(f"[{sym}] {dr.upper()} 슬롯 {oc.MAX_POS_DIR} 초과 — 스킵")
        shadow_emit(sig, "SLOT", sctx, logger)
        return False
    # ③ A-3/R6 포트폴리오 방향 노출 캡 — 전 심볼 통틀어 동시 동일방향 ≤ MAX_CONCURRENT_DIR.
    #    크립토 상관(≈BTC 0.8+)으로 동시 다발 동일방향이 함께 무너지는 손실 클러스터를 차단.
    if open_idx["glob_dir"].get(dr, 0) >= oc.MAX_CONCURRENT_DIR:
        logger.info(f"[{sym}] 동시 {dr.upper()} {oc.MAX_CONCURRENT_DIR}개 한도 — 상관 노출 차단")
        shadow_emit(sig, "DIRCAP", sctx, logger)
        return False
    notion.log_signal(sig)        # 기록
    notify.notify_signal(sig)     # ALERT_ENABLED=true 일 때만 발송
    open_idx["keys"].add((sym, pol, dr))
    open_idx["dir_count"][(sym, dr)] = open_idx["dir_count"].get((sym, dr), 0) + 1
    open_idx["glob_dir"][dr] = open_idx["glob_dir"].get(dr, 0) + 1
    return True


def main():
    logger = setup_logging()
    logger.info("=" * 55)
    logger.info(f"🤖 ORTHO 봇 시작 — {timeutil.now_kst_str()}")
    logger.info(f"   {oc.summary()}")
    logger.info("=" * 55)

    exchange = od.create_exchange()

    single = os.getenv("SINGLE_SYMBOL", "").strip()
    symbols = [single] if single else oc.SYMBOLS

    # 현재 OPEN 색인 1회 로드 → 중복 셋업·슬롯 초과 진입 차단(과적재 방지)
    open_idx = notion.open_index()
    logger.info(f"   기존 OPEN: {len(open_idx['keys'])}건 (중복 진입 차단 기준)")
    sctx = make_shadow_ctx(open_idx, logger)   # FN 측정용 Shadow 컨텍스트(비활성 시 None)

    # 측정용 컬럼(OBI·Taker Slope·Funding %·Axis Vec) 멱등 보장 — 라이브·Shadow DB 둘 다.
    if oc.SCALP_FEATS and notion.enabled():
        notion.ensure_schema()                                   # 라이브 DB
        if sctx is not None:
            notion.ensure_schema(oc.NOTION_SHADOW_DB_ID)         # Shadow DB

    # 1) 수집: 전 심볼 후보를 모은다(라이브=중복-OPEN 선거름 / Shadow=차단 셋업 별도).
    candidates, shadows = [], []
    for sym in symbols:
        lv, sh = collect_candidates(exchange, sym, open_idx, logger)
        candidates += lv
        shadows += sh
    # 1-B) 유니버스 확장(Tier A): 신규 심볼은 라이브 미승격 — 통과 신호까지 EXPLORE:UNIVERSE로 Shadow만.
    #   심볼 추가는 독립 표본↑(과적합 불가). single 매트릭스 모드에선 건너뜀(중복 방지). Shadow 활성 시만.
    if sctx is not None and not single and oc.EXPLORE_SYMBOLS:
        logger.info(f"   🌑 유니버스 확장 {len(oc.EXPLORE_SYMBOLS)}심볼 → Shadow 적재(라이브 미승격)")
        for sym in oc.EXPLORE_SYMBOLS:
            lv, sh = collect_candidates(exchange, sym, open_idx, logger)
            for sig in lv:                         # 통과 신호도 라이브가 아니라 Shadow로 강등
                sig["shadow"] = True
                sig["blocked_by"] = "EXPLORE:UNIVERSE"
            shadows += lv + sh
    # 2) R6 상관 디둡: 동일 실행 내 후보를 품질(RR) 우선 정렬 → 방향 캡이 최선부터 채워지게.
    #    그리디(심볼 알파벳순)면 평범한 후보가 슬롯을 차지하고 우수 후보가 잘릴 수 있음.
    if oc.CORR_DEDUP:
        candidates.sort(key=lambda s: s.get("rr") or 0.0, reverse=True)
    # 3) 승인: 슬롯·방향 캡 적용하며 기록·알림(캡 차단분은 admit 내부에서 Shadow 적재).
    total = sum(1 for sig in candidates if admit(sig, open_idx, sctx, logger))
    # 4) 엔진 차단점(거부권/추격)에서 모인 Shadow 후보 적재 — RR 우선(쿼터 효율).
    shadow_n = 0
    if sctx is not None and shadows:
        shadows.sort(key=lambda s: s.get("rr") or 0.0, reverse=True)
        for sig in shadows:
            if shadow_emit(sig, sig.get("blocked_by", "?"), sctx, logger):
                shadow_n += 1

    mode = "알림ON" if oc.ALERT_ENABLED else "학습기간(알림OFF)"
    logger.info("-" * 55)
    sh_msg = f" | 🌑shadow {shadow_n}건" if sctx is not None else ""
    logger.info(f"📊 완료 — 신호 {total}건{sh_msg} | {mode}")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
