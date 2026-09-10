from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

from providency.config import Settings
from providency.startup import StartupProgress


def loading_screen() -> None:
    progress = StartupProgress(Settings.from_env().data_dir)
    current = progress.read()
    if (
        not os.environ.get("PROVIDENCY_LAUNCH_ID")
        or current.get("launch_id") != os.environ.get("PROVIDENCY_LAUNCH_ID")
        or current.get("stage") == "READY"
    ):
        return

    @st.fragment(run_every=0.5)
    def loading() -> None:
        state = progress.read()
        if state.get("stage") == "READY":
            st.rerun(scope="app")
        st.title("Providency está iniciando")
        for column, name in zip(st.columns(2), ("connection", "charts"), strict=True):
            column.image(str(Path(__file__).parent / "loading_assets" / f"{name}.gif"))
        st.progress(float(state.get("progress", 0)), text=state.get("message", "Preparando…"))
        st.caption("A barra avança conforme cada serviço fica disponível.")
        if state.get("stage") == "ERROR":
            st.error(state["message"])
        if st.button("Encerrar Providency", key="cancel_startup"):
            progress.cancel(str(state.get("launch_id", "")))
            st.info("Encerrando os processos de inicialização…")

    loading()
    st.stop()


def render_onboarding(action: Callable[..., Any]) -> None:
    state = action("GET", "/onboarding")
    if state is None:
        return
    stage = state["stage"]
    st.title("Vamos preparar o seu BOT")
    st.caption("1 · Navegador e Vector   →   2 · Gráficos   →   3 · Telegram   →   4 · Conferência")

    def advance(command: str, payload: dict[str, Any] | None = None) -> None:
        with st.spinner("Conferindo…"):
            result = action("POST", f"/onboarding/{command}", payload or {})
        if result is not None:
            st.rerun()

    if stage == "WELCOME":
        st.write(
            "Conecte seu navegador, confira os gráficos abertos e escolha o destino no Telegram."
        )
        if st.button("Preparar conexões", type="primary"):
            advance("start")
        if st.button(
            "Iniciar BOT", help="Compatibilidade: este botão apenas prepara as conexões."
        ):
            advance("start")
    elif stage == "VECTOR_WAIT":
        st.subheader("Abra a Vector no seu navegador")
        st.write(
            "Entre na sua conta e abra os gráficos nas abas internas da Vector. "
            "Cada gráfico mantém seu próprio ativo e período."
        )
        if not state["connected"]:
            st.info(
                "Instale a extensão local Providency no Brave, Chrome ou Edge e conecte-a abaixo."
            )
            with st.expander("Como instalar a extensão uma vez"):
                st.write(
                    "Abra a página de extensões do navegador, ative o modo de desenvolvedor, "
                    "clique em Carregar sem compactação e selecione a pasta browser-extension "
                    "distribuída com o Providency. Abra o ícone da extensão "
                    "e informe a porta e o código."
                )
            if st.button("Gerar código de conexão"):
                paired = action("POST", "/onboarding/pair", {})
                if paired:
                    st.session_state["pair_code"] = paired["code"]
            if st.session_state.get("pair_code"):
                st.code(st.session_state["pair_code"])
                st.caption(
                    f"Porta local: {Settings.from_env().api_port}. Código válido para esta conexão."
                )
        if st.button("OK, a Vector está aberta", type="primary"):
            advance("vector-check")
    elif stage == "VECTOR_REVIEW":
        st.session_state.pop("pair_code", None)
        st.subheader("Confira os gráficos encontrados")
        st.dataframe(
            [{"Ativo": c["symbol"], "Período": c["timeframe"]} for c in state["charts"]],
            hide_index=True,
            width="stretch",
        )
        if st.button("Confirmar gráficos e continuar", type="primary"):
            advance("vector-confirm")
    elif stage == "TELEGRAM_WAIT":
        st.subheader("Abra o Telegram Web")
        st.write(
            "No mesmo navegador conectado, abra o Telegram Web e selecione o grupo "
            "que deverá receber os avisos."
        )
        if st.button("OK, o Telegram está aberto", type="primary"):
            advance("telegram-check")
    elif stage == "TELEGRAM_REVIEW":
        st.subheader("Confira o destino identificado")
        index = st.selectbox(
            "Conversa aberta",
            range(len(state["destinations"])),
            format_func=lambda i: state["destinations"][i]["name"],
        )
        st.caption("A permissão de envio do BOT será configurada na próxima etapa.")
        if st.button("Confirmar destino", type="primary"):
            advance("telegram-confirm", {"index": index})
    elif stage == "READY":
        st.success("Conexões conferidas!")
        st.write(f"{len(state['charts'])} gráficos · Telegram: {state['destination']['name']}")
        st.info(
            "Próxima etapa: definir os parâmetros e validar o envio pelo BOT. "
            "O monitoramento ainda não foi iniciado."
        )
    if stage != "WELCOME" and st.button("Recomeçar configuração"):
        advance("start")
