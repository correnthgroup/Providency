# Estrela da tarde

## Definicao canonica

Reversao baixista de tres velas apos alta: corpo longo altista, corpo pequeno de
qualquer cor com gap de corpo para cima e corpo longo baixista que se separa do
corpo central e fecha abaixo do ponto medio do primeiro corpo.

## Geometria e sequencia

`candle: 0` e altista e longo; `candle: 1` e pequeno e seu `BODY_LOW` fica
acima do `BODY_HIGH` de 0; `candle: 2` e baixista e longo, seu `BODY_HIGH` fica
abaixo do `BODY_LOW` de 1 e `C2 < (O0 + C0) / 2`. Para reproducibilidade,
as velas longas exigem corpo/faixa >= 0,60 e a star, corpo/faixa <= 0,30. Esses
cortes quantificam termos qualitativos das fontes. O contexto exige tres
fechamentos ascendentes.

## Formacao

O candidato surge apos a vela 0 longa e altista e passa a `FORMING` quando a
star pequena fecha acima do corpo 0. Falha se a terceira vela nao for baixista,
nao penetrar alem da metade ou perder a separacao dos corpos.

## Confirmacao e gatilho

A terceira vela confirma intrinsecamente o padrao. A politica conservadora do
Providency exige depois rompimento da minima das tres velas, acrescido do buffer,
para o gatilho short.

## Stop e invalidacao

Stop acima da maior maxima da formacao mais o buffer do Risk Engine. A regra e
estrutural e evita escolher arbitrariamente apenas uma das velas. Fechamento
acima da maxima invalida; rompimento antes da entrada cancela o setup.

## Contexto e limitacoes

Bulkowski observou reversao baixista em 72% e desempenho geral 4 de 103. Porem,
os melhores numeros de movimento e alvo da pagina ocorreram em breakouts para
cima, contrarios a operacao short, e uma combinacao tinha somente 63 amostras.
Em mercados continuos, gaps sao raros. Nao interpretar o rank geral como taxa
de sucesso da operacao baixista.

## Evidencia e fixtures

Bulkowski testou centenas de formacoes perfeitas em acoes e separou mercado e
direcao do breakout, em base de quase cinco milhoes de linhas de candle. O
breakout exige fechamento alem da maxima ou minima. Nison e Morris sustentam a
definicao; a terceira vela e a confirmacao da formacao.

As tres fixtures OHLC sinteticas cobrem caso positivo, falha na penetracao de
50% e formacao incompleta, todas no split `development`. Casos de fronteira
entram como negativos. Elas exercitam a regra deterministica. A coleta visual
será ampliada por período e condição de mercado com o uso diário.

## Resultado da evidencia publicada

- Reversao baixista observada: 72%.
- Rank de reversao: 10 de 103.
- Rank de desempenho geral: 4 de 103.
- Melhor taxa de alvo: 50%, bull market, breakout para cima.
- Limitacao residual: melhor desempenho agregado nao prova vantagem short.
