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

COMO ENCERRAR
-------------
Fechar a aba do navegador não encerra o aplicativo.
PARAR interrompe somente a sessão de observação.
Use "Encerrar Providency" no menu lateral para finalizar os processos e fechar
a Vector controlada. O histórico e o perfil de login são preservados.
Se houver operação pendente ou posição demo sob gerenciamento, resolva-a antes
de encerrar. Este botão não envia ordens e não é uma parada de emergência.

PRIMEIRO USO
------------
1. Abra Início e use "Preparar conexões".
2. Abra a Vector Web e conclua o login manual no navegador conectado.
3. Confirme todos os gráficos encontrados e o destino Telegram.
4. Em Configurações, defina o intervalo e confira o catálogo.
5. Em Atividades, use "Iniciar Bot" para a primeira captura imediata.

OBSERVAÇÃO INFORMATIVA
----------------------
O modo OBSERVATION_ONLY lê somente screenshots novas e envia um resumo de
padrões ao chat confirmado. Ele não cria propostas, aprovações, WOULD_EXECUTE,
ordens, sincronizações financeiras, stops ou fechamentos.

TOKEN TELEGRAM
--------------
Salve o token completo uma vez em Configurações > Telegram. Ele permanece no
cofre do Windows após fechar ou atualizar o aplicativo. O campo fica vazio
para não expor a credencial; o painel informa se ela está salva. Um erro de
rede não apaga o token. Valores malformados não substituem a credencial salva.
Para testes sem persistência, escolha "Usar somente nesta sessão". Essa opção
mantém o token apenas na memória do motor e o remove ao encerrar o aplicativo.

SEGURANÇA
---------
O bundle inicia em OBSERVATION_ONLY: nenhuma ordem financeira chega à Vector. O modo DEMO
exige configuração explícita e somente aceita uma conta demo positivamente
verificada. SAFE_STOP bloqueia nova exposição quando posição ou proteção não
podem ser compreendidas.

Os dados ficam no diretório local do Providency. Não remova a pasta de dados
durante uma sessão.

BUILD 0.11.1
--------------------------
Captura pelo navegador conectado, imagens de referência no catálogo e
diagnóstico de Telegram/cofre. Consulte docs/observation-0.11.1-validation.md
no código-fonte para evidências e pendências do teste real.
O bundle macOS é produzido e validado somente pelo workflow nativo em runner
macOS.
