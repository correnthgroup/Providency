# ruff: noqa: E501
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime
from typing import Any

import streamlit as st

from providency import __version__
from providency.config import Settings
from providency.onboarding_ui import loading_screen, render_onboarding
from providency.ui_model import (
    OPERATION_STAGES,
    desired_applied_rows,
    human_datetime,
    human_event_message,
    operational_stage,
)


def api_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    settings = Settings.from_env()
    request = urllib.request.Request(
        f"{settings.api_url}{path}",
        method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", {})
            message = detail.get("human_message") if isinstance(detail, dict) else str(detail)
        except (OSError, ValueError, AttributeError):
            message = None
        raise RuntimeError(message or "A solicitação foi bloqueada por segurança.") from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError("O Core Engine não está disponível.") from exc


def action(method: str, path: str, payload: dict[str, Any] | None = None) -> Any | None:
    try:
        return api_request(method, path, payload)
    except RuntimeError as exc:
        st.error(str(exc))
        return None


def inject_style() -> None:
    st.markdown(
        """
        <style>
        :root { --ink:#10231d; --muted:#61716b; --green:#147a5a; --mint:#e9f5f0;
                --gold:#c68a2b; --paper:#f7f8f5; --line:#dce5df; --danger:#b33a3a; }
        .stApp { background:var(--paper); color:var(--ink); }
        [data-testid="stSidebar"] { background:#10231d; }
        [data-testid="stSidebar"] * { color:#f5f8f6 !important; }
        [data-testid="stMetric"] { background:white; border:1px solid var(--line);
            border-radius:14px; padding:14px 16px; box-shadow:0 4px 18px rgba(16,35,29,.04); }
        .brand { display:flex; align-items:center; gap:12px; margin:2px 0 22px; }
        .brand-mark { width:44px; height:44px; display:grid; place-items:center; border-radius:14px;
            background:linear-gradient(135deg,#1b8f6a,#d4a74e); color:white; font-size:25px; }
        .brand-name { font:700 1.45rem Georgia,serif; color:white; line-height:1; }
        .brand-sub { color:#a9beb5; font-size:.78rem; margin-top:5px; }
        .hero { border-radius:20px; padding:24px 28px; margin-bottom:18px;
            color:white; background:linear-gradient(120deg,#10231d 0%,#174f3f 68%,#9b762d 140%); }
        .hero h1 { font:700 2rem Georgia,serif; margin:0 0 5px; color:white; }
        .hero p { margin:0; color:#d7e3de; }
        .eyebrow { color:#d6ae60; text-transform:uppercase; letter-spacing:.12em;
            font-size:.72rem; font-weight:700; margin-bottom:8px; }
        .status-pill { display:inline-flex; align-items:center; gap:8px; padding:6px 11px;
            border-radius:999px; font-weight:700; font-size:.78rem; margin-top:15px;
            background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18); }
        .dot { width:8px; height:8px; border-radius:50%; background:#64d7a8; }
        .roadmap { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin:12px 0 24px; }
        .stage { min-height:58px; padding:10px; border-radius:11px; background:white;
            border:1px solid var(--line); color:var(--muted); font-size:.75rem; }
        .stage b { display:block; color:var(--ink); margin-bottom:3px; }
        .stage.done { background:var(--mint); border-color:#9bcdb9; }
        .stage.current { background:#fff7e6; border-color:#d8ad5a; box-shadow:0 0 0 2px #f4dfb3; }
        .section-intro { color:var(--muted); max-width:780px; margin-top:-8px; margin-bottom:18px; }
        .event { background:white; border-left:4px solid #79a995; padding:11px 14px;
            border-radius:0 10px 10px 0; margin:7px 0; }
        .event.warning { border-left-color:#d0a14b; }
        .event.error { border-left-color:var(--danger); }
        .event small { color:var(--muted); }
        div.stButton > button[kind="primary"] { background:var(--green); border-color:var(--green); }
        @media(max-width:900px) { .roadmap { grid-template-columns:repeat(2,1fr); } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_hero(state: dict[str, Any], mode: str) -> None:
    running = state["engine_state"] == "RUNNING"
    session = state.get("session")
    status = "Em operação" if running else "Parado e seguro"
    detail = (
        f"Sessão iniciada em {human_datetime(session['started_at'])}"
        if session
        else "Aguardando comando do operador"
    )
    st.markdown(
        f"""
        <div class="hero">
          <div class="eyebrow">Control plane local · {mode}</div>
          <h1>Providency</h1><p>{detail}</p>
          <div class="status-pill"><span class="dot"></span>{status}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def configuration_payload(desired: dict[str, Any]) -> dict[str, Any] | None:
    with st.form("analysis-configuration"):
        st.markdown("#### Mercado e leitura visual")
        st.caption("Preenchido pelo botão Atualizar a partir da tela atual da Vector.")
        c1, c2, c3 = st.columns(3)
        symbol = c1.text_input("Ativo", value=str(desired.get("symbol") or ""), disabled=True)
        primary = c2.text_input("Timeframe principal", value=str(desired.get("primary_timeframe") or ""), disabled=True)
        quantity = c3.number_input("Quantidade", min_value=0.0, value=float(desired.get("quantity") or 0), format="%.8f", disabled=True)
        t1, t2 = st.columns(2)
        tick_size = float(desired.get("tick_size") or 0)
        t1.text_input("Tamanho do tick", value=str(tick_size) if tick_size > 0 else "Não identificado", disabled=True)
        tick_value = float(desired.get("tick_value") or 0)
        t2.text_input("Valor do tick por unidade", value=str(tick_value) if tick_value > 0 else "Não identificado", disabled=True)
        st.markdown("#### Limites definidos por você")
        r1, r2, r3 = st.columns(3)
        min_rr = r1.number_input("Risco/retorno mínimo", min_value=0.1, value=float(desired.get("min_rr") or 2))
        max_trades = r2.number_input("Máximo de operações diárias", min_value=0, value=int(desired.get("max_trades") or 0))
        max_losses = r3.number_input("Perdas consecutivas", min_value=0, value=int(desired.get("max_consecutive_losses") or 0))
        with st.expander("Ajustes avançados de leitura e limites"):
            st.caption("Estas regras não são informadas pela tela de ordens. Valores existentes são preservados ao atualizar.")
            c1, c2 = st.columns(2)
            context = c1.text_input("Timeframe de contexto", value=str(desired.get("context_timeframe") or ""))
            trailing = c2.text_input("Timeframe de trailing", value=str(desired.get("trailing_timeframe") or ""))
            ma_enabled = st.checkbox("Usar médias móveis", value=desired.get("short_ma_period") is not None)
            m1, m2, m3 = st.columns(3)
            short_ma = m1.number_input("Média curta", min_value=1, value=int(desired.get("short_ma_period") or 7), disabled=not ma_enabled)
            long_ma = m2.number_input("Média longa", min_value=1, value=int(desired.get("long_ma_period") or 70), disabled=not ma_enabled)
            pivot_window = m3.number_input("Janela de pivô", min_value=1, value=int(desired.get("pivot_window") or 3))
            stop_buffer = st.number_input("Buffer do stop (ticks)", min_value=0, value=int(desired.get("stop_buffer_ticks") or 0))
            max_loss = st.number_input("Perda máxima da sessão", min_value=0.0, value=float(desired.get("max_session_loss") or 0), help="Limite financeiro escolhido pelo operador; não é igual ao saldo disponível.")
            tolerance = st.number_input("Tolerância de suporte/resistência", min_value=0.0, value=float(desired.get("support_resistance_tolerance") or 0))
        submitted = st.form_submit_button("Salvar parâmetros", type="primary")
    if not submitted:
        return None
    return {
        "schema_version": 1, "symbol": symbol.strip(), "primary_timeframe": primary.strip(),
        "context_timeframe": context.strip(), "trailing_timeframe": trailing.strip(),
        "short_ma_period": int(short_ma) if ma_enabled else None,
        "long_ma_period": int(long_ma) if ma_enabled else None,
        "quantity": float(quantity), "tick_size": float(tick_size), "tick_value": float(tick_value),
        "stop_buffer_ticks": int(stop_buffer), "min_rr": float(min_rr),
        "max_trades": int(max_trades), "max_consecutive_losses": int(max_losses),
        "max_session_loss": float(max_loss), "pivot_window": int(pivot_window),
        "support_resistance_tolerance": float(tolerance),
    }


def render_settings(
    configuration: dict[str, Any], vector: dict[str, Any], telegram: dict[str, Any]
) -> None:
    st.header("Parâmetros e Configurações")
    if st.button("Atualizar", type="primary", help="Lê a tela de ordens e o saldo da Vector sem alterar ordens."):
        with st.spinner("Lendo a tela de ordens da Vector…"):
            refreshed = action("POST", "/configuration/refresh", {})
        configuration = api_request("GET", "/configuration")
        if refreshed is not None:
            st.success("Dados da Vector atualizados. Seus limites de risco foram preservados.")
    snapshot = configuration.get("vector_snapshot")
    if snapshot:
        st.caption(f"{snapshot['source']} · leitura em {human_datetime(snapshot['observed_at'])}")
        if not configuration.get('browser_connected'):
            st.warning('A extensão foi desconectada. Os valores abaixo são da última leitura.')
        balance = snapshot.get('balance')
        b1, b2, b3 = st.columns(3)
        b1.metric('Saldo disponível na Vector',
                  (format(balance["amount"], ",.2f").replace(",", "_").replace(".", ",").replace("_", ".") + " " + balance["currency"]) if balance else 'Não identificado')
        b2.metric('Preço da ordem', f"{snapshot['order']['price']['text']} {snapshot['order']['price']['unit']}")
        b3.metric('Total da ordem', f"{snapshot['order']['total']['text']} {snapshot['order']['total']['unit']}")
        st.caption('O saldo é uma referência de gerenciamento; não altera seu limite de perda.')
        with st.expander('Gráficos encontrados e origem dos dados'):
            st.dataframe([{'Ativo': c['symbol'], 'Período': c['timeframe'], 'Ativo na tela': c['active']}
                          for c in snapshot['charts']], hide_index=True, width='stretch')
            for note in snapshot['notes']:
                st.caption(note)
            st.caption('Ativo, período principal e quantidade vêm da tela atual. '
                       'O tamanho do tick vem do incremento declarado no controle de preço. '
                       'Contexto, trailing e médias dependem da configuração de leitura e não são presumidos.')
    else:
        st.info('Após conectar a Vector no Início, deixe a tela de ordens aberta e clique em Atualizar.')
    st.markdown(
        '<p class="section-intro">Defina o que o Providency deve observar e compare cada parâmetro com o estado realmente confirmado na Vector.</p>',
        unsafe_allow_html=True,
    )
    desired = configuration["desired"]
    if desired.get("missing_fields"):
        st.warning("Primeiro uso: complete os parâmetros para liberar a avaliação de candidatos.")
        completed = 3 if vector.get("state") == "OPEN" else 2
        st.progress(completed / 4, text=f"Configuração inicial · etapa {completed} de 4")
    payload = configuration_payload(desired)
    if payload is not None and action("PUT", "/configuration", payload) is not None:
        st.success("Parâmetros salvos em uma nova versão auditável.")
        st.rerun()
    st.divider()
    st.subheader("Desejado × Aplicado")
    st.dataframe(
        desired_applied_rows(desired, configuration.get("applied")),
        width="stretch",
        hide_index=True,
    )
    left, right = st.columns(2)
    with left:
        st.markdown("#### Vector Web")
        if configuration.get('browser_connected'):
            st.success("Extensão conectada ao navegador atual.")
        else:
            st.info("Conecte a extensão no Início para atualizar os dados da Vector.")
    with right:
        st.markdown("#### Telegram")
        missing = telegram["configuration"].get("missing_fields", [])
        if missing:
            st.warning("Configuração pendente: " + ", ".join(missing))
        else:
            st.success("Identidade e polling configurados; token protegido e mascarado.")
        with st.expander("Como configurar"):
            st.write(
                "Informe chat, usuário e TTL pelas variáveis `PROVIDENCY_TELEGRAM_*`; o token permanece no cofre de credenciais do sistema operacional."
            )


def render_roadmap(current: int) -> None:
    cards = []
    for index, stage in enumerate(OPERATION_STAGES):
        css = "done" if index < current else "current" if index == current else ""
        marker = "Concluído" if index < current else "Agora" if index == current else "A seguir"
        cards.append(f'<div class="stage {css}"><b>{index + 1}. {stage}</b>{marker}</div>')
    st.markdown('<div class="roadmap">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_operation(
    state: dict[str, Any],
    configuration: dict[str, Any],
    vector: dict[str, Any],
    execution: dict[str, Any],
    candidates: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    protections: list[dict[str, Any]],
) -> None:
    st.header("Gerenciamento e Operação")
    st.markdown(
        '<p class="section-intro">Comande a sessão, acompanhe o estágio atual e intervenha sem perder os bloqueios de segurança.</p>',
        unsafe_allow_html=True,
    )
    running = state["engine_state"] == "RUNNING"
    current = operational_stage(
        running=running,
        missing_configuration=bool(configuration["desired"].get("missing_fields")),
        vector_state=str(vector.get("state")),
        candidates=candidates,
        approvals=approvals,
        operations=operations,
        protections=protections,
    )
    render_roadmap(current)
    if execution["mode"] == "DRY_RUN":
        st.info(
            "DRY_RUN ativo — uma aprovação registra WOULD_EXECUTE; nenhuma ordem chega à Vector."
        )
    else:
        st.warning("MODO DEMO — somente a conta demo positivamente verificada pode receber ações.")
    if execution.get("blocking_protection"):
        st.error("SAFE_STOP — novas entradas estão bloqueadas até a proteção ser compreendida.")
    run_col, stop_col, emergency_col = st.columns(3)
    if (
        run_col.button("▶  INICIAR", type="primary", disabled=running, width="stretch")
        and action("POST", "/run") is not None
    ):
        st.rerun()
    if (
        stop_col.button("■  PARAR", disabled=not running, width="stretch")
        and action("POST", "/stop") is not None
    ):
        st.rerun()
    active = next((item for item in operations if item.get("status") == "FILLED"), None)
    ready = bool(active and execution["mode"] == "DEMO" and st.session_state.get("emergency_ok"))
    emergency_path = (
        f"/operations/{active['operation_id']}/emergency-stop" if active is not None else ""
    )
    if (
        emergency_col.button(
            "⚠  PARADA DE EMERGÊNCIA", disabled=not ready, width="stretch"
        )
        and action("POST", emergency_path, {"confirm_demo_close": True}) is not None
    ):
        st.session_state["emergency_ok"] = False
        st.rerun()
    st.checkbox(
        "Confirmo o cancelamento/fechamento da posição demo ativa",
        key="emergency_ok",
        disabled=active is None or execution["mode"] != "DEMO",
        help="A confirmação explícita evita acionamento acidental da emergência.",
    )
    session = state.get("session")
    if session:
        st.caption(
            f"Sessão {session['id'][:8]} · iniciada em {human_datetime(session['started_at'])}"
        )
    st.subheader("Ciclo de observação")
    o1, o2, o3 = st.columns(3)
    if o1.button("1 · Sincronizar configuração", disabled=vector.get("state") != "OPEN"):
        synced = action("POST", "/vector/sync")
        if synced is not None:
            st.success(
                "Configuração aplicada e verificada."
                if synced["matches_desired"]
                else "Há divergências na Vector."
            )
    if o2.button("2 · Capturar e analisar", disabled=vector.get("state") != "OPEN"):
        captured = action("POST", "/vector/capture-analysis")
        if captured is not None:
            st.session_state["last_analysis_capture"] = captured
            st.rerun()
    if o3.button(
        "3 · Avaliar candidato",
        disabled=not running or "last_analysis_capture" not in st.session_state,
    ):
        captured = st.session_state["last_analysis_capture"]
        if (
            action(
                "POST",
                "/candidate/evaluate",
                {
                    "analysis_capture_id": captured["analysis_capture_id"],
                    "pattern_detection_id": captured["pattern_detection_id"],
                },
            )
            is not None
        ):
            st.rerun()
    captured = st.session_state.get("last_analysis_capture")
    if captured:
        pattern = captured["pattern"]
        st.markdown(f"#### Última leitura: `{pattern['status']}`")
        st.write(pattern["reason"])
        paths = [
            item.get("path")
            for item in (captured["primary"], captured["context"])
            if item.get("path")
        ]
        if paths:
            st.image(paths, caption=["Principal", "Contexto"][: len(paths)])
    if candidates:
        latest = candidates[0]
        st.subheader("Proposta mais recente")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Decisão", latest["decision"])
        k2.metric("Entrada", latest.get("entry") or "—")
        k3.metric("Stop", latest.get("stop") or "—")
        k4.metric("Risco", latest.get("risk_amount") or "—")
        if (
            latest["decision"] == "ALLOWED"
            and not approvals
            and st.button("Enviar proposta imutável ao Telegram", type="primary")
            and action("POST", f"/approvals/{latest['candidate_id']}") is not None
        ):
            st.rerun()
        if latest.get("blocking_reasons"):
            st.warning("Bloqueios: " + "; ".join(latest["blocking_reasons"]))
        with st.expander("Detalhes técnicos da proposta"):
            st.json(latest)


def _within_date(value: str | None, start: date, end: date) -> bool:
    try:
        current = datetime.fromisoformat((value or "").replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return start <= current <= end


def render_activities_results(
    state: dict[str, Any],
    events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    protections: list[dict[str, Any]],
) -> None:
    st.header("Atividades e Resultados")
    st.markdown(
        '<p class="section-intro">Leia o que aconteceu em linguagem operacional, revise evidências e acompanhe a qualidade do padrão.</p>',
        unsafe_allow_html=True,
    )
    activity_tab, result_tab, review_tab, pattern_tab = st.tabs(
        ["Atividades", "Resultados", "Revisão humana", "Catálogo de padrões"]
    )
    with activity_tab:
        level = st.selectbox("Nível", ["Todos", "INFO", "WARNING", "ERROR"])
        visible = (
            events if level == "Todos" else [item for item in events if item["level"] == level]
        )
        if not visible:
            st.info("Nenhuma atividade para este filtro.")
        for event in visible:
            st.markdown(
                f'<div class="event {event["level"].lower()}"><b>{human_event_message(event["message"])}</b><br><small>{human_datetime(event["created_at"])} · {event["component"]}</small></div>',
                unsafe_allow_html=True,
            )
            if event["level"] == "ERROR":
                with st.expander("Impacto e detalhes técnicos"):
                    st.json(event.get("details", {}))
    with result_tab:
        session = state.get("session")
        report = action("GET", f"/sessions/{session['id']}/report") if session else None
        funnel = (report or {}).get("funnel", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Capturas", funnel.get("captures", 0))
        c2.metric("Padrões confirmados", funnel.get("confirmed_patterns", 0))
        c3.metric("Candidatos", funnel.get("candidates", len(candidates)))
        c4.metric("Operações demo", funnel.get("filled_operations", 0))
        today = date.today()
        dates = st.date_input("Período", value=(today.replace(day=1), today))
        start, end = dates if isinstance(dates, tuple) and len(dates) == 2 else (today, today)
        rows = [
            {
                "Data": human_datetime(item.get("created_at")),
                "Candidato": item["candidate_id"][:8],
                "Decisão": item["decision"],
                "Entrada": item.get("entry"),
                "Stop": item.get("stop"),
                "Risco": item.get("risk_amount"),
            }
            for item in candidates
            if _within_date(item.get("created_at"), start, end)
        ]
        st.dataframe(rows, width="stretch", hide_index=True)
        if session and st.button("Exportar relatório reproduzível"):
            exported = action("POST", f"/sessions/{session['id']}/report/export")
            if exported:
                st.success(f"Relatório salvo localmente em {exported['path']}")
    with review_tab:
        detections = action("GET", "/pattern/detections?limit=100") or []
        if not detections:
            st.info("Capture e analise um gráfico para iniciar a revisão humana.")
        else:
            selected = st.selectbox(
                "Detecção",
                detections,
                format_func=lambda item: (
                    f"{human_datetime(item['created_at'])} · {item['result']} · {item['id'][:8]}"
                ),
            )
            st.write(selected["reason"])
            if st.button("Criar cópia sanitizada para revisão"):
                sanitized = action(
                    "POST", "/evidence/sanitize", {"detection_id": selected["id"], "redactions": []}
                )
                if sanitized:
                    st.session_state["review_evidence"] = sanitized
            evidence = st.session_state.get("review_evidence")
            if evidence:
                st.image(evidence["path"], caption="Evidência local sanitizada")
                label = st.selectbox(
                    "Classificação",
                    [
                        "TRUE_POSITIVE",
                        "FALSE_POSITIVE",
                        "FALSE_NEGATIVE",
                        "TRUE_NEGATIVE",
                        "NO_DECISION",
                        "OPERATIONAL_FAILURE",
                    ],
                    help="Classifique o que o sistema viu sem reescrever a decisão histórica.",
                )
                notes = st.text_area("Observações", max_chars=500)
                if st.button("Salvar revisão", disabled=not notes.strip()):
                    saved = action(
                        "POST",
                        "/reviews",
                        {
                            "detection_id": selected["id"],
                            "label": label,
                            "notes": notes,
                            "reviewer_id": "local:operator",
                            "evidence_sha256": evidence["source_sha256"],
                            "evidence_path": evidence["path"],
                        },
                    )
                    if saved:
                        st.success(f"Revisão salva como versão {saved['revision']}.")
    with pattern_tab:
        metrics = action("GET", "/metrics/patterns/bearish_engulfing") or {}
        precision = metrics.get("precision", {})
        p1, p2, p3 = st.columns(3)
        p1.metric("Padrão ativo", "Engolfo de baixa")
        p2.metric(
            "Precisão revisada",
            "Amostra insuficiente"
            if precision.get("value") is None
            else f"{precision['value'] * 100:.1f}%",
        )
        p3.metric("Revisões", metrics.get("reviewed_total", 0))
        st.info(
            "O catálogo é composto por pacotes externos versionados. A interface lista, explica e mede padrões; ela não funciona como editor visual de estratégias."
        )
        with st.expander("Detalhes de qualidade"):
            st.json(metrics)
        if operations or protections or approvals:
            with st.expander("Estado operacional persistido"):
                st.write(
                    {"aprovações": approvals, "operações": operations, "proteções": protections}
                )


st.set_page_config(page_title="Providency", page_icon="🛡️", layout="wide")
inject_style()
loading_screen()
with st.sidebar:
    st.markdown(
        '<div class="brand"><div class="brand-mark">P</div><div><div class="brand-name">Providency</div><div class="brand-sub">observe · confirme · proteja</div></div></div>',
        unsafe_allow_html=True,
    )
    page = st.radio("Navegação", ("Início", "Configurações", "Atividades"), label_visibility="collapsed")
    st.divider()
    st.caption(f"Providency {__version__} · dados no dispositivo")
    st.caption("Fechar esta aba mantém o aplicativo em execução.")
    if st.button(
        "Encerrar Providency",
        width="stretch",
        help="Encerra a sessão, a Vector controlada e os processos do aplicativo. Seus dados são preservados.",
    ) and action("POST", "/shutdown", {"confirm": True}) is not None:
        st.session_state["shutdown_requested"] = True
if st.session_state.get("shutdown_requested"):
    st.success("Encerramento solicitado. O Providency está finalizando os processos; você pode fechar esta aba.")
    st.stop()
if page == "Início":
    render_onboarding(action)
    st.stop()
try:
    app_state = api_request("GET", "/state")
    configuration_state = api_request("GET", "/configuration")
    vector_health = api_request("GET", "/vector/health")
    execution_state = api_request("GET", "/execution/status")
    telegram_state = api_request("GET", "/telegram/status")
    candidate_items = api_request("GET", "/candidates?limit=100")
    approval_items = api_request("GET", "/approvals?limit=100")
    operation_items = api_request("GET", "/operations?limit=100")
    protection_items = api_request("GET", "/protections?limit=100")
    event_items = api_request("GET", "/events?limit=200")
except RuntimeError as exc:
    st.error(str(exc))
    st.info("Inicie o Providency pelo launcher e aguarde o Core Engine ficar disponível.")
    st.stop()
render_hero(app_state, execution_state["mode"])
if page == "Configura\u00e7\u00f5es":
    render_settings(configuration_state, vector_health, telegram_state)
    with st.expander("Operação avançada"):
        render_operation(
            app_state,
            configuration_state,
            vector_health,
            execution_state,
            candidate_items,
            approval_items,
            operation_items,
            protection_items,
        )
else:
    render_activities_results(
        app_state, event_items, candidate_items, approval_items, operation_items, protection_items
    )
