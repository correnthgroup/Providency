# Providency

Aplicativo local para observação visual supervisionada de mercado. O runtime
fornece Core Engine local, persistência SQLite, sessões, eventos, API FastAPI,
controle Streamlit e launcher single-instance. O `VectorAdapter` abre a Vector
Web em um perfil persistente do Chromium e produz capturas observacionais. O
primeiro vertical slice do Pattern Matcher detecta candles tradicionais e
avalia o pacote `bearish_engulfing`. O preflight de contexto e risco produz
candidatos locais imutáveis e sempre falha fechado quando falta evidência.

## Desenvolvimento

Requisitos:

- `uv`;
- Python 3.12, gerenciado pelo `uv`.

```powershell
uv sync --dev
uv run playwright install chromium
uv run pytest
uv run ruff check .
uv run providency-launcher
```

O launcher inicia o Core Engine e a UI. O estado inicial é `STOPPED`; uma sessão
operacional só começa após `RUN`.

## Captura da Vector Web

Defina `PROVIDENCY_VECTOR_URL` no ambiente. Abra a Vector pela UI, conclua o
login manualmente e mantenha o gráfico primário visível. Como a Vector Web não
oferece um contrato público de DOM, os seletores ficam centralizados nas
variáveis `PROVIDENCY_VECTOR_*_SELECTOR` documentadas em `.env.example` e devem
ser calibrados para o layout observado.

Perfil, screenshots WebP e metadados operacionais ficam sob
`PROVIDENCY_DATA_DIR`, fora do Git. A captura retorna `NO_DECISION` se detectar
login, carregamento, modal sobre o gráfico, área pequena/ausente, múltiplos
gráficos candidatos, ativo ilegível ou timeframe ilegível. A URL configurada
deve ser HTTPS.

## Pattern Matcher

A ação **Capture and analyze** faz uma captura nova, extrai caixas e OHLC
relativo dos candles por OpenCV, aplica os predicados do Pattern Package e
mostra `MATCH`, `FORMING` ou `NO_MATCH` com uma razão legível. A evidência no
SQLite contém hash, caminho da screenshot, versão do pacote, candles e medidas;
a imagem continua somente no filesystem.

O catálogo `patterns/` é incluído no wheel. `PROVIDENCY_PATTERNS_DIR` pode
apontar explicitamente para outro checkout durante desenvolvimento.

## Contexto e risco

O incremento 4 mantém configuração desejada e estado aplicado separados. A
sincronização de símbolo, timeframe e médias usa PRE → ACTION → POST com
seletores DOM explícitos. A escala de preços usa ao menos dois labels visíveis
com geometria, sem OCR. O fluxo captura primary/context e verifica a restauração
do primary antes de permitir um candidato.

O candidato congela hashes das capturas, detecção, configuração, escala,
confluências, entrada, stop, quantidade configurada, risco, RR e limites da
sessão. `MISSING` ou `FAIL` bloqueia a proposta. Esta versão não expõe aprovação,
Telegram, quantidade na Vector nem qualquer ação financeira.

## Limites atuais

- Mudanças não financeiras exigem seletores explícitos calibrados para o layout.
- O detector visual ainda requer calibração com screenshots reais sanitizadas.
- Nenhuma mensagem Telegram.
- Nenhuma ordem financeira.
- Nenhuma credencial é preenchida ou armazenada pelo Providency.

Segredos e dados operacionais não pertencem ao Git. Use variáveis de ambiente e
o credential store do sistema nas etapas que introduzirem integrações.


O diretório `patterns/` contém os Pattern Packages iniciais e seu contrato de
predicados. Ainda não há um Git root independente; por enquanto, o conteúdo é
versionado pelo workspace Correnth.
