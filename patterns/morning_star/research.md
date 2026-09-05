# Estrela da manha

## Definicao canonica

Reversao altista de tres velas apos queda: corpo longo baixista, corpo pequeno
de qualquer cor com gap de corpo para baixo e corpo longo altista que se separa
do corpo central e fecha pelo menos alem do ponto medio do primeiro corpo.

## Geometria e sequencia

`candle: 0` e baixista e longo; `candle: 1` e pequeno e seu `BODY_HIGH` fica
abaixo do `BODY_LOW` de 0; `candle: 2` e altista e longo, seu `BODY_LOW` fica
acima do `BODY_HIGH` de 1 e `C2 > (O0 + C0) / 2`. Para tornar os termos
qualitativos reproduziveis, as velas longas exigem corpo/faixa >= 0,60 e a star
exige corpo/faixa <= 0,30. O contexto exige tres fechamentos descendentes.

## Formacao

O estado `FORMING` comeca apos a vela 0 longa e baixista e se fortalece quando
a star pequena fecha abaixo do corpo 0. O candidato falha se a terceira vela
nao for altista, nao fechar alem do ponto medio ou perder a separacao dos corpos.

## Confirmacao e gatilho

A terceira vela e a confirmacao intrinseca segundo Nison e Morris. O Providency
adota entrada conservadora somente no rompimento da maxima das tres velas. O
rompimento deve superar o nivel pelo buffer e ocorrer depois do fechamento da
terceira vela.

## Stop e invalidacao

Stop abaixo da menor minima do padrao, com buffer de tick/spread/slippage do
Risk Engine. Essa e uma politica estrutural: a minima representa o extremo da
tentativa de reversao. Fechamento abaixo dela invalida a leitura; rompimento
antes da entrada cancela o gatilho.

## Contexto e limitacoes

Bulkowski observou reversao altista em 78% e desempenho geral 12 de 103. O
melhor movimento de dez dias publicado foi de baixa, nao de alta, e usou apenas
108 ocorrencias naquela combinacao. A direcao de breakout nao equivale a lucro.
Em BTC e outros mercados continuos, os gaps canonicos tornam o padrao raro; uma
versao sem gap seria outro pacote e precisaria de fonte e avaliacao proprias.

## Evidencia e fixtures

Bulkowski testou centenas de ocorrencias perfeitas em acoes, separando mercados
bull/bear e breakout para cima/baixo, dentro de uma base de quase cinco milhoes
de candles. Breakout e fechamento acima da maxima ou abaixo da minima do padrao.
Nison e Morris sustentam a definicao e a confirmacao na terceira vela.

As tres fixtures OHLC sinteticas cobrem caso positivo, falha na penetracao de
50% e formacao incompleta, todas no split `development`. Casos de fronteira
entram como negativos. Elas exercitam a regra deterministica. A coleta visual
será ampliada por período e condição de mercado com o uso diário.

## Resultado da evidencia publicada

- Reversao altista observada: 78%.
- Rank de reversao: 6 de 103.
- Rank de desempenho geral: 12 de 103.
- Melhor taxa de alvo: 49%, bear market, breakout para cima.
- Limitacao residual: gaps raros em cripto e evidencia quantitativa em acoes.
