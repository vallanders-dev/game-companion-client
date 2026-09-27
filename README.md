# Parça (beta)

Seu parceiro de jogo por voz. Aperte uma tecla enquanto joga, faça a
pergunta em voz alta e ouça a resposta, sem sair do jogo.

Você precisa de um **token de testador**, fornecido por quem organiza o beta.

## Instalação (uma vez só)

1. **Instale o Python:** baixe em [python.org/downloads](https://www.python.org/downloads/)
   e, na primeira tela do instalador, **marque "Add python.exe to PATH"**.
2. **Baixe o instalador do Parça:**
   [instalar-parca.cmd](https://github.com/vallanders-dev/game-companion-client/releases/latest/download/instalar-parca.cmd)
3. **Dê dois cliques nele.** Se o Windows mostrar "O Windows protegeu o
   computador", clique em **Mais informações** e depois em **Executar assim
   mesmo**. A instalação leva alguns minutos e cria o ícone **Parça** na
   área de trabalho.
4. O Parça abre sozinho no final. Na janela que aparecer, **cole o seu
   token** e clique em **Conectar**. Pronto!

O token fica salvo no seu usuário do Windows. É só seu: não compartilhe.

## Como usar

Abra pelo ícone **Parça** na área de trabalho e jogue normalmente.

- **F8**: pergunta por voz (captura a tela e grava a pergunta)
- **F6**: anota algo pra ele lembrar depois (ele confirma por voz)
- **BACK + LB** no controle: mesma coisa que o F8
- Aperte **F8** ou **F6** enquanto ele fala para interromper e mandar outro comando
- Terminou? Um "valeu, Parça" encerra a conversa

Dicas importantes:

- Rode o jogo em **janela sem bordas** ou **tela cheia em janela**. Em tela
  cheia exclusiva as teclas podem não ser detectadas.
- **Se o jogo roda como administrador**, abra o Parça do mesmo jeito:
  clique com o botão direito no ícone **Parça** e escolha **Executar como
  administrador**. Senão o Windows esconde as teclas do Parça enquanto o
  jogo está em foco, e o F8/F6 não fazem nada.
- Se a internet cair ou o servidor reiniciar, é só perguntar de novo: o
  Parça se reconecta sozinho.
- Deixe só **um** Parça aberto. Se abrir outro, ele avisa e fecha sozinho.

## Atualizações

Toda vez que você abre o Parça ele verifica se há uma versão nova e se
atualiza sozinho.

## Trocar de token

Se o servidor recusar o seu token, a janela aparece de novo. Para trocar
manualmente, apague o arquivo `%APPDATA%\Parca\settings.json` e abra o
Parça de novo.

## Instalando pelo Git (opcional)

Quem preferir: `git clone` este repositório, crie um ambiente com
`python -m venv .venv`, rode `.venv\Scripts\pip install -r requirements.txt`
e abra pelo `Parca.cmd`. As atualizações chegam do mesmo jeito.
