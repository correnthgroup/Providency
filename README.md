# Providency

Aplicativo local para observação visual supervisionada de mercado. O primeiro
incremento fornece o Core Engine local, persistência SQLite, sessões, eventos,
API FastAPI, controle Streamlit e launcher single-instance.

## Desenvolvimento

Requisitos:

- `uv`;
- Python 3.12, gerenciado pelo `uv`.

```powershell
uv sync --dev
uv run pytest
uv run ruff check .
uv run providency-launcher
```

O launcher inicia o Core Engine e a UI. O estado inicial é `STOPPED`; uma sessão
operacional só começa após `RUN`.

## Limites atuais

- Nenhum acesso à Vector Web.
- Nenhuma mensagem Telegram.
- Nenhuma ordem financeira.
- Somente estado local e fluxo de controle do Incremento 1.

Segredos e dados operacionais não pertencem ao Git. Use variáveis de ambiente e
o credential store do sistema nas etapas que introduzirem integrações.


O diretório `patterns/` contém os Pattern Packages iniciais e seu contrato de
predicados. Ainda não há um Git root independente; por enquanto, o conteúdo é
versionado pelo workspace Correnth.
