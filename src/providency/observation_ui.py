# ruff: noqa: E501
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from providency.telegram_ui import render_telegram


def _call(
    action: Callable[[str, str, dict[str, Any] | None], Any | None],
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any | None:
    return action(method, path, payload)


def render_observation_settings(
    action: Callable[[str, str, dict[str, Any] | None], Any | None],
    configuration: dict[str, Any],
    vector: dict[str, Any],
    telegram: dict[str, Any],
) -> None:
    st.header("Configurações")
    st.caption("A leitura visual é informativa e não altera parâmetros nem envia ordens à Vector.")
    st.subheader("Mercado e Leitura Visual")
    snapshot = configuration.get("vector_snapshot") or {}
    cols = st.columns(3)
    cols[0].text_input(
        "Ativo", value=str(snapshot.get("symbol") or "Não identificado"), disabled=True
    )
    cols[1].text_input(
        "Timeframe",
        value=str(snapshot.get("primary_timeframe") or "Não identificado"),
        disabled=True,
    )
    cols[2].text_input(
        "Momento da leitura",
        value=str(snapshot.get("observed_at") or "Não identificado"),
        disabled=True,
    )
    if st.button("Atualizar leitura da Vector", help="Lê a tela atual sem aplicar configuração."):
        _call(action, "POST", "/configuration/refresh", {})
        st.rerun()
    if st.button("Atualizar", help="Compatibilidade: atualiza a leitura sem aplicar parâmetros."):
        _call(action, "POST", "/configuration/refresh", {})
        st.rerun()
    if snapshot:
        fields = snapshot.get("fields", {})
        balance = snapshot.get("balance")
        if balance:
            formatted_balance = (
                f"{balance['amount']:,.2f} {balance['currency']}"
                .replace(",", "_")
                .replace(".", ",")
                .replace("_", ".")
            )
            st.metric("Saldo disponível na Vector", formatted_balance)
        legacy = st.columns(3)
        legacy[0].number_input(
            "Quantidade", value=float(fields.get("quantity", 0)), disabled=True
        )
        desired = configuration.get("desired", {})
        legacy[1].number_input(
            "Risco/retorno mínimo", value=float(desired.get("min_rr") or 2), disabled=True
        )
        legacy[2].number_input(
            "Máximo de operações diárias",
            value=int(desired.get("max_trades") or 0),
            disabled=True,
        )

    observation = _call(action, "GET", "/observation/configuration") or {}
    current = observation.get(
        "interval", {"value": 5, "unit": "minutes", "timezone": "America/Sao_Paulo"}
    )
    state = _call(action, "GET", "/observation/state") or {}
    stopped = state.get("phase") in {"STOPPED", "ERROR"}
    st.subheader("Intervalo de captura")
    with st.form("observation-interval"):
        a, b, c = st.columns(3)
        value = a.number_input(
            "Intervalo",
            min_value=1,
            value=int(current.get("value", 5)),
            step=1,
            disabled=not stopped,
        )
        unit = b.selectbox(
            "Unidade",
            ["minutes", "hours", "days", "months"],
            index=["minutes", "hours", "days", "months"].index(str(current.get("unit", "minutes"))),
            disabled=not stopped,
        )
        timezone = c.text_input(
            "Fuso IANA",
            value=str(current.get("timezone", "America/Sao_Paulo")),
            disabled=not stopped,
        )
        save = st.form_submit_button("Salvar intervalo", disabled=not stopped)
    if save:
        payload = {
            "interval": {"value": int(value), "unit": unit, "timezone": timezone},
            "charts": observation.get("charts", []),
            "enabled_pattern_ids": observation.get("enabled_pattern_ids", []),
        }
        if _call(action, "PUT", "/observation/configuration", payload) is not None:
            st.success("Intervalo salvo. A primeira captura ocorre ao iniciar o Bot.")

    st.subheader("Catálogo")
    catalog = _call(action, "GET", "/patterns/catalog") or []
    for item in catalog:
        with st.expander(
            f"{item.get('display_name_pt', item.get('id'))} · v{item.get('version', '')}"
        ):
            st.caption(f"{item.get('name_en', '')} · família {item.get('family', '—')}")
            st.write(item.get("description") or "Sem descrição.")
            st.caption(
                f"{item.get('illustration', {}).get('label', 'Ilustração didática')} · {item.get('quality', {}).get('observed_precision', 'Amostra insuficiente')}"
            )
            st.json(item.get("quality", {}))

    st.subheader("Telegram")
    telegram_config = telegram.get("configuration", {})
    st.write(
        {
            "bot": telegram_config.get("chat_id") and "configurado" or "não configurado",
            "destino": telegram_config.get("chat_id") or "Não identificado",
        }
    )
    with st.expander("Configurar destino Telegram"):
        render_telegram(telegram, action)


def render_observation_activities(
    action: Callable[[str, str, dict[str, Any] | None], Any | None],
) -> None:
    st.header("Atividades")
    state = _call(action, "GET", "/observation/state") or {}
    phase = str(state.get("phase", "STOPPED"))
    st.metric(
        "Estado",
        {"STOPPED": "Bot parado", "WAITING": "Aguardando intervalo de captura"}.get(phase, phase),
    )
    next_capture = state.get("next_capture_at")
    if next_capture:
        st.info(f"Próxima captura: {next_capture} · fuso {state.get('timezone', '')}")
    start, stop = st.columns(2)
    if start.button("Iniciar Bot", type="primary", disabled=phase not in {"STOPPED", "ERROR"}):
        _call(action, "POST", "/observation/start")
        st.rerun()
    if stop.button("Parar Bot", disabled=phase in {"STOPPED"}):
        _call(action, "POST", "/observation/stop")
        st.rerun()

    summary_tab, log_tab = st.tabs(["Resumo", "Log de atividades"])
    cycles = _call(action, "GET", "/observation/cycles?limit=100") or []
    with summary_tab:
        if not cycles:
            st.info("Bot parado. Nenhum ciclo de observação registrado.")
        for cycle in cycles:
            st.markdown(f"**{cycle.get('status')}** · {cycle.get('started_at')}")
            st.write(cycle.get("summary", ""))
    with log_tab:
        events = _call(action, "GET", "/events?limit=200") or []
        for event in events:
            if event.get("component") == "observation":
                st.write(f"{event.get('created_at')} · {event.get('message')}")
