from collections.abc import Callable
from typing import Any

import streamlit as st

from providency.telegram import valid_token_format


def render_telegram(state: dict[str, Any], action: Callable[..., Any]) -> None:
    if st.session_state.pop("clear_telegram_token", False):
        st.session_state["telegram_token"] = ""
    st.markdown("#### Telegram")
    configuration = state["configuration"]
    if configuration.get("missing_fields"):
        st.info("Configure o bot e confirme quem poderá aprovar as propostas abaixo.")
    else:
        st.success("Destino e aprovador salvos. Use Testar bot para verificar o token.")
        st.caption(f"Chat: {configuration['chat_id']} · Aprovador: {configuration['user_id']}")
    destination = state.get("browser_destination")
    if destination:
        st.caption(f"Conversa conferida no navegador: {destination['name']}")
    with st.container(border=True):
        st.markdown("**1 · Token e conexão do bot**")
        credential = action("GET", "/telegram/credential-status") or {}
        if credential.get("session_only"):
            st.success("Token disponível somente nesta sessão, sem gravação no cofre.")
        if credential.get("saved"):
            if credential.get("valid_format"):
                st.success(
                    "Token salvo no cofre deste computador. "
                    "Não é preciso informar novamente ao reabrir o aplicativo."
                )
            else:
                st.error(
                    "Há um valor salvo no cofre, mas ele não tem o formato de um token "
                    "do Telegram. Corrija-o abaixo."
                )
        st.markdown(
            "Abra [BotFather](https://t.me/BotFather), envie `/newbot` e escolha um nome "
            "de usuário terminado em `bot`. Adicione o bot ao grupo Providency. "
            "Cole o token abaixo e escolha entre uso temporário e salvamento no cofre."
        )
        token = st.text_input(
            "Token do bot",
            type="password",
            key="telegram_token",
            help="Credencial fornecida pelo BotFather. Não é salva no banco.",
        )
        credential_disabled = state.get("session_active", False)
        save_token = st.button("Salvar token no cofre", disabled=credential_disabled)
        temporary_token = st.button("Usar somente nesta sessão", disabled=credential_disabled)
        if save_token or temporary_token:
            st.session_state.pop("telegram_bot_username", None)
            st.session_state.pop("telegram_destinations", None)
            if not token.strip():
                st.error("Cole o token fornecido pelo BotFather.")
            elif not valid_token_format(token.strip()):
                st.error(
                    "Token inválido: copie somente o token completo fornecido pelo BotFather "
                    "(número, dois-pontos e chave). O cofre não foi alterado."
                )
            elif temporary_token:
                if action("PUT", "/telegram/session-token", {"token": token.strip()}):
                    st.session_state["telegram_token_temporary"] = True
            else:
                try:
                    import keyring

                    keyring.set_password("Providency", "telegram-bot-token", token.strip())
                except Exception:
                    st.error("Não foi possível salvar no cofre de credenciais deste computador.")
                else:
                    st.session_state["telegram_token_saved"] = True
            if st.session_state.get("telegram_token_saved") or st.session_state.get(
                "telegram_token_temporary"
            ):
                st.session_state["clear_telegram_token"] = True
                st.rerun()
        if st.session_state.pop("telegram_token_temporary", False):
            st.success("Token ativado apenas em memória. Será removido ao encerrar o aplicativo.")
        if st.session_state.pop("telegram_token_saved", False):
            st.success("Token salvo no cofre.")
        if st.button("Testar bot"):
            with st.spinner("Conferindo o bot no Telegram…"):
                result = action("GET", "/telegram/health")
            if result:
                st.session_state["telegram_bot_username"] = result["bot_username"]
                st.success(f"Bot conectado: @{result['bot_username']}")
    st.markdown("**2 · Identificar destino e aprovador**")
    st.caption(
        "Adicione o bot ao grupo de destino. O aprovador deve enviar o comando abaixo, "
        "sem espaço antes do @. Depois clique em Identificar e confira grupo e usuário. "
        "Não é necessário o bot responder uma mensagem ao /start."
    )
    bot_username = st.session_state.get("telegram_bot_username")
    if bot_username:
        st.code(f"/start@{bot_username}", language=None)
    else:
        st.info("Clique em Testar bot para obter o comando com o nome correto.")
    if st.button("Identificar grupo e usuário"):
        with st.spinner("Buscando o comando enviado ao bot…"):
            discovered = action("GET", "/telegram/discovery")
        st.session_state["telegram_destinations"] = discovered or []
        if discovered == []:
            st.warning("Nenhum /start recente encontrado. Envie o comando ao bot e tente de novo.")
    choices = st.session_state.get("telegram_destinations", [])
    if choices:
        selected = st.selectbox(
            "Grupo e usuário encontrados",
            range(len(choices)),
            format_func=lambda i: (
                f"{choices[i]['chat_name']} ({choices[i]['chat_id']}) · "
                f"{choices[i]['user_name']} ({choices[i]['user_id']})"
            ),
        )
        ttl = st.number_input(
            "Prazo para aprovar (segundos)",
            min_value=1,
            value=int(configuration.get("approval_ttl_seconds", 60)),
            help="Depois deste prazo, a proposta expira.",
        )
        if st.button("Confirmar destino e aprovador", type="primary"):
            choice = choices[selected]
            result = action(
                "PUT",
                "/telegram/configuration",
                {
                    "chat_id": choice["chat_id"],
                    "user_id": choice["user_id"],
                    "approval_ttl_seconds": int(ttl),
                    "recheck_price_tolerance_ticks": configuration.get(
                        "recheck_price_tolerance_ticks", 1
                    ),
                },
            )
            if result:
                st.session_state.pop("telegram_destinations", None)
                st.rerun()
