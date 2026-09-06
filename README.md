# Providency

Aplicativo local para observação visual supervisionada de mercado. O runtime
fornece Core Engine local, persistência SQLite, sessões, eventos, API FastAPI,
controle Streamlit e launcher single-instance. O `VectorAdapter` abre a Vector
Web em um perfil persistente do Chromium e produz capturas observacionais. O
primeiro vertical slice do Pattern Matcher detecta candles tradicionais e
avalia o pacote `bearish_engulfing`. O preflight de contexto e risco produz
candidatos locais imutáveis e sempre falha fechado quando falta evidência. O
incremento 5 envia propostas imutáveis pelo Telegram e, após SIM, só registra
`WOULD_EXECUTE` depois de uma nova captura e rechecagem completa. O incremento 6
mantém `DRY_RUN` como padrão e adiciona execução supervisionada exclusivamente em
conta demo, com intenção idempotente, PRE/ACTION/POST e reconciliação explícita.
O incremento 8 adiciona revisão humana append-only, cópias sanitizadas de
evidência, relatórios reproduzíveis de sessão e métricas com denominadores e
versões explícitas. A interface `0.8.1` organiza o uso diário em **Parâmetros e
Configurações**, **Gerenciamento e Operação** e **Atividades e Resultados**, com
linguagem de operador, roteiro do ciclo e comparação entre estado desejado e
aplicado.

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
sessão. `MISSING` ou `FAIL` bloqueia a proposta antes da aprovação por Telegram.

## Execução demo

`PROVIDENCY_EXECUTION_MODE` aceita somente `DRY_RUN` ou `DEMO`. Não existe modo
live nem fallback para conta real. Em `DEMO`, todos os seletores específicos de
conta, quantidade, compra/venda, ordem e posição precisam ser calibrados
explicitamente; ausência ou valor desconhecido bloqueia antes do envio.

O `operation_id` é persistido antes da única tentativa de ordem. Timeout,
resposta ambígua, ordem pendente ou parcial bloqueiam uma nova exposição até que
a reconciliação observe um estado inequívoco.

## Proteção, trailing e recuperação

Cada posição demo preenchida cria uma política versionada ligada ao
`operation_id`. O stop inicial, breakeven e trailing usam seletores explícitos,
candles fechados do timeframe configurado e PRE → ACTION → POST. LONG nunca
reduz o stop e SHORT nunca o eleva; candle repetido, aberto ou ilegível não move
a proteção.

Posição, quantidade ou stop divergente produz `SAFE_STOP`, alerta visível e
bloqueio de nova exposição. O restart reconcilia posição e proteção antes de
retomar. `EMERGENCY_STOP` só existe por comando humano explícito na API/UI e faz
no máximo uma tentativa auditada de cancelar a proteção e encerrar a posição
demo; resultado ambíguo nunca recebe retry automático.

## Uso diário e melhoria

O menu **Atividades e Resultados** mostra o funil operacional, permite exportar
o relatório local, cria revisões versionadas e exibe precision/recall sem
ocultar denominadores ou insuficiência de amostra. A política completa de
baseline, sanitização, retenção, taxonomia e calibração está em
`DAILY_OPERATIONS.md`.

Screenshots sanitizadas ficam em `sanitized-evidence/` sob
`PROVIDENCY_DATA_DIR`; relatórios ficam em `reports/`. A retenção padrão é 30
dias e pode ser ajustada por `PROVIDENCY_EVIDENCE_RETENTION_DAYS`.

## Limites atuais

- Mudanças não financeiras exigem seletores explícitos calibrados para o layout.
- O detector visual ainda requer calibração com screenshots reais sanitizadas.
- O bot do Telegram aceita somente botões SIM/NÃO vinculados ao chat e usuário
  configurados; comandos livres não são processados.
- Ordens são possíveis somente quando `DEMO` é configurado explicitamente e a
  conta aplicada é provada como demo antes e depois das ações.
- Nenhuma credencial é preenchida ou armazenada pelo Providency.

Segredos e dados operacionais não pertencem ao Git. Grave o token com
`python -m keyring set Providency telegram-bot-token`; configure apenas chat,
usuário e TTL pelas variáveis documentadas em `.env.example`. O token não é
aceito por variável de ambiente, API ou SQLite.

## Pacote nativo

O build nativo usa PyInstaller e inclui Streamlit, Playwright, Chromium, Pattern
Packages e identidade visual:

```text
Providency/
├── Providency WIN.exe  (build Windows)
├── Providency MAC.app  (build macOS)
├── assets/
│   └── robot/providency.svg
└── README.txt
```

Cada plataforma gera seu próprio pacote; os dois artefatos são reunidos na
distribuição final. Para construir na plataforma atual:

```powershell
uv run python packaging/build_release.py
```


O diretório `patterns/` contém os Pattern Packages iniciais e seu contrato de
predicados. Ainda não há um Git root independente; por enquanto, o conteúdo é
versionado pelo workspace Correnth.
