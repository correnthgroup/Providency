PROVIDENCY
==========

Aplicativo local de observação visual e execução supervisionada.

COMO INICIAR
------------
Windows: abra "Providency WIN.exe".
macOS: abra "Providency MAC.app".

Se o Providency já estiver em execução, um segundo clique apenas abrirá o painel
existente. O aplicativo mantém um único Core Engine, banco SQLite, listener do
Telegram e navegador controlado por perfil do sistema operacional.

PRIMEIRO USO
------------
1. Abra Parâmetros e Configurações.
2. Preencha o ativo, os timeframes e os limites de gerenciamento.
3. Abra a Vector Web e conclua o login manual.
4. Configure o Telegram no cofre/ambiente local.
5. Compare Desejado × Aplicado antes de iniciar uma sessão.

SEGURANÇA
---------
O modo padrão é DRY_RUN: nenhuma ordem financeira chega à Vector. O modo DEMO
exige configuração explícita e somente aceita uma conta demo positivamente
verificada. SAFE_STOP bloqueia nova exposição quando posição ou proteção não
podem ser compreendidas.

Os dados ficam no diretório local do Providency. Não remova a pasta de dados
durante uma sessão.
