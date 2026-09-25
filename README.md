# Companheiro de Jogo (beta)

Um companheiro de jogo por voz. Aperte uma tecla enquanto joga, faça a
pergunta em voz alta e ouça a resposta, sem sair do jogo.

Este repositório é só o **cliente**: ele grava sua voz, captura a tela do
jogo e toca a resposta. Quem responde é o servidor do teste. Você precisa
de um **token de testador**, fornecido por quem organiza o beta.

## Requisitos

- Windows 10 ou 11
- [Python 3.14](https://www.python.org/downloads/) (marque "Add python.exe to PATH" na instalação)
- [Git](https://git-scm.com/download/win), para receber as atualizações automáticas
- Microfone e fones/caixas de som

## Instalação

No PowerShell:

```powershell
git clone https://github.com/vallanders-dev/game-companion-client.git
cd game-companion-client
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Depois copie `client\.env.example` para `client\.env` e preencha:

```
SERVER_URL=<endereço do servidor, recebido junto com o token>
SERVER_AUTH_TOKEN=<seu token de testador>
```

Não compartilhe o seu token.

## Como usar

```powershell
cd game-companion-client
.venv\Scripts\Activate.ps1
python -m client.main
```

- **F8**: pergunta por voz (captura a tela e grava a pergunta)
- **F6**: anota algo pra ele lembrar depois (ele confirma por voz)
- **BACK + LB** no controle: mesma coisa que o F8
- Aperte **F8** de novo enquanto ele fala para interromper e perguntar outra coisa

Dicas importantes:

- Rode o jogo em **janela sem bordas** ou **tela cheia em janela**. Em tela
  cheia exclusiva as teclas podem não ser detectadas.
- **Se o jogo roda como administrador, abra o PowerShell como
  administrador também** (clique com o botão direito > "Executar como
  administrador"). Senão o Windows esconde as teclas do programa enquanto
  o jogo está em foco, e o F8/F6 não fazem nada.

## Atualizações

Toda vez que você abre o programa ele verifica se há uma versão nova e se
atualiza sozinho. Para desligar, coloque `AUTO_UPDATE=false` no
`client\.env`.
