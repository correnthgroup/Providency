# Template de Pattern Package

Copie esta pasta para `patterns/<pattern_id>/` e substitua todos os valores
marcados como `TODO`. O diretório `_template` não é um padrão instalável e
deve ser ignorado pelo catálogo e pelo checker estrutural.

## Checklist de pesquisa

- [ ] O nome canônico e a família foram definidos.
- [ ] A geometria ou sequência de candles foi descrita sem ambiguidade.
- [ ] A regra de formação foi separada da regra de confirmação.
- [ ] Gatilho, stop e invalidação foram definidos.
- [ ] Contextos compatíveis foram justificados.
- [ ] As fontes foram registradas em `references.yaml`.
- [ ] Os exemplos positivos, negativos e em formação foram classificados.
- [ ] Os casos esperados foram registrados em `tests/expected_cases.yaml`.
- [ ] As fixtures existentes exercitam casos positivos, negativos e em formação.
- [ ] `enabled` expressa a decisão atual de uso do padrão.

## Convenção da sequência

Em `pattern.yaml`, `candle: 0` é o candle mais antigo da janela e o último
índice é o candle mais recente. Use somente campos e operadores suportados
pelo Pattern Matcher. Se uma regra nova for necessária, ela deve primeiro ser
implementada e documentada no contrato do engine; texto livre no YAML não é
executável.

## Ativação

Não existe ciclo de aprovação do pacote. `enabled: true` coloca o padrão em uso
e `enabled: false` o retira do matcher sem apagar pesquisa, fixtures ou histórico.
O checker estrutural aponta apenas arquivos ou campos quebrados; ele não decide
se um padrão pode ser utilizado.

## Arquivos

- `pattern.yaml`: contrato executável e metadados do catálogo.
- `research.md`: definição, hipóteses, limitações e método de pesquisa.
- `references.yaml`: fontes que sustentam a definição.
- `dataset.yaml`: procedência, rótulo e divisão de cada amostra.
- `positive/`: exemplos que satisfazem a definição.
- `negative/`: exemplos visualmente parecidos que não satisfazem a definição.
- `forming/`: exemplos ainda não confirmados.
- `tests/expected_cases.yaml`: casos determinísticos para regressão.
