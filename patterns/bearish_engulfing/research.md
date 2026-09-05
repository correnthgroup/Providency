# Engolfo de baixa

## Definicao canonica

Padrao de duas velas depois de tendencia de alta. A primeira e altista. A
segunda e baixista e seu corpo real engloba o corpo real da primeira; as sombras
nao precisam ser engolfadas. Sem alta previa, a CMT Association alerta que a
leitura de reversao pode ser invalida.

## Geometria e sequencia

Para `candle: 0` e `candle: 1`: `C0 > O0`, `C1 < O1`, `O1 > C0` e
`C1 < O0`. O pacote usa a versao estrita para impedir empate integral. O
contexto exige tres fechamentos ascendentes antes de `candle: 0`.

## Formacao

Depois da vela compradora, o candidato fica `FORMING` se a vela seguinte abre
estritamente acima do fechamento anterior e negocia para baixo. Deixa de ser
candidato se fechar altista ou sem cobrir integralmente o corpo anterior.

## Confirmacao e gatilho

O engolfo completo fica `CONFIRMED` no fechamento de `candle: 1`. O gatilho
short posterior e o rompimento da menor minima do padrao mais o buffer. A taxa
de Bulkowski, diferentemente desse gatilho intrabar, classificou breakout por
fechamento alem do extremo.

## Stop e invalidacao

O stop fica acima da maior maxima das duas velas, acrescida do buffer definido
pelo Risk Engine. Esse ponto e a invalidacao estrutural conservadora; usar
somente a maxima da segunda vela pode deixar a primeira maxima desprotegida.
Fechamento acima da maxima invalida a leitura. Antes da entrada, qualquer
rompimento dessa maxima cancela o candidato.

## Contexto e limitacoes

Priorizar repique contra uma tendencia primaria baixista, resistencia e volume
relevante. Bulkowski observou reversao baixista em 79% dos casos, mas desempenho
geral 91 de 103: a reversao tende a ser curta. Heinz et al. encontraram poder
preditivo de curto prazo para abertura e maxima do S&P 500, mas nao para o
fechamento. Logo, 79% nao e taxa de trades vencedores nem justifica operar sem
confirmacao, custos e controle de risco.

## Evidencia e fixtures

Bulkowski analisou acoes em mercados bull e bear dentro de quase cinco milhoes
de linhas de candles e classificou breakout por fechamento alem do extremo do
padrao. A pagina informa cerca de 20.000 ocorrencias deste padrao. Heinz et al.
fizeram analise estatistica especifica dos Engulfings no indice S&P 500. As duas
fontes medem previsao/direcao, nao rentabilidade liquida universal.

No pacote, `positive/001.yaml`, `negative/001.yaml` e `forming/001.yaml` usam
OHLC sintetico e ficam no split `development`. A rotulagem segue literalmente
as regras; fronteiras ambiguas sao negativas. Esses casos exercitam a logica
deterministica. A coleta visual será ampliada com o uso diário.

## Resultado da evidencia publicada

- Reversao baixista observada por Bulkowski: 79%.
- Rank de reversao: 5 de 103.
- Rank de desempenho geral: 91 de 103.
- Melhor taxa de alvo: 76%, em bear market com breakout para baixo.
- Evidencia adicional: poder de previsao de abertura/maxima, nao de fechamento.
- Limitacao residual: evidencia concentrada em acoes; custos e BTC nao cobertos.
