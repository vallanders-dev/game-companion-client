# Ombri (beta)

Seu parceiro de jogo por voz. Aperte uma tecla enquanto joga, faça a
pergunta em voz alta e ouça a resposta, sem sair do jogo.

Você precisa de um **token de testador**, fornecido por quem organiza o beta.

## Instalação (uma vez só)

1. **Baixe o instalador:**
   [Ombri-Setup.exe](https://github.com/vallanders-dev/game-companion-client/releases/latest/download/Ombri-Setup.exe)
   (cerca de 70 MB). Não precisa instalar Python nem mais nada: vem tudo junto.
2. **Abra o arquivo.** Como o Ombri ainda é um programa novo, o Windows pode
   mostrar "O Windows protegeu o computador": clique em **Mais informações**
   e depois em **Executar assim mesmo**.
3. Escolha se quer um **ícone na área de trabalho** e clique em **Instalar**.
   Leva menos de um minuto e não pede senha de administrador. O **Ombri**
   aparece no menu Iniciar.
4. O Ombri abre sozinho no final. Na janela que aparecer, escolha o
   **idioma** e a **voz** (o botão **Ouvir** toca uma amostra), **cole o
   seu token** e clique em **Conectar**. Pronto!

Já tinha instalado pelo `instalar-parca.cmd`? Rode o Ombri-Setup.exe por cima:
ele troca a instalação antiga pela nova e mantém o seu token.

O token fica salvo no seu usuário do Windows. É só seu: não compartilhe.

## Como usar

Abra o **Ombri** pelo menu Iniciar (ou pelo ícone na área de trabalho). Aparece a janela do Ombri
(status, jogo detectado, perguntas restantes do dia). Pode fechar ou
minimizar: ele continua rodando na **bandeja do Windows**, perto do relógio.
Clique no ícone da bandeja para abrir a janela de novo, **pausar** o Ombri
(F8/F6 param de funcionar até você retomar) ou **sair**. Durante o jogo fica
só uma bolinha no canto da tela, que muda de cor quando ele ouve, pensa e fala.

- **F8**: pergunta por voz (captura a tela e grava a pergunta)
- **F6**: anota algo pra ele lembrar depois (ele confirma por voz)
- **BACK + LB** no controle: mesma coisa que o F8
- Aperte **F8** ou **F6** enquanto ele fala para interromper e mandar outro comando
- Terminou? Um "valeu, Ombri" encerra a conversa

Dicas importantes:

- Rode o jogo em **janela sem bordas** ou **tela cheia em janela**. Em tela
  cheia exclusiva as teclas podem não ser detectadas.
- **Se o jogo roda como administrador**, abra o Ombri do mesmo jeito:
  clique com o botão direito no **Ombri** (menu Iniciar ou área de trabalho) e
  escolha **Executar como administrador**. Senão o Windows esconde as teclas do Ombri enquanto o
  jogo está em foco, e o F8/F6 não fazem nada.
- Se a internet cair ou o servidor reiniciar, é só perguntar de novo: o
  Ombri se reconecta sozinho.
- Deixe só **um** Ombri aberto. Se abrir outro, ele avisa e fecha sozinho.
- Se o jogo roda como administrador, o Ombri mostra um aviso com o botão
  **Reabrir como admin**: é só clicar e aceitar a pergunta do Windows.
- Algo estranho? O registro do que aconteceu fica em
  `%LOCALAPPDATA%\Parca\app\output\parca.log`: mande esse arquivo pra quem
  organiza o beta.

## Idioma e voz

O Ombri fala **português** (vozes Raquel e Yuri) ou **inglês** (Lily,
Ivanna e Hale). O idioma vale pra conversa inteira: ele entende e responde
nesse idioma. Pra trocar, clique em **Configurações** na janela do Ombri ou
no ícone da bandeja. A nova voz vale a partir da próxima pergunta.

## Atualizações

Toda vez que você abre o Ombri ele verifica se há uma versão nova e se
atualiza sozinho.

## Desinstalar

Configurações do Windows → **Aplicativos** → **Ombri** → **Desinstalar**. O seu
token fica guardado, caso você instale de novo.

## Trocar de token

Se o servidor recusar o seu token, a janela aparece de novo. Para trocar
manualmente, clique em **Configurações** na janela do Ombri (ou na bandeja)
e cole o token novo.

## Instalando pelo Git (opcional)

Quem preferir: `git clone` este repositório, crie um ambiente com
`python -m venv .venv`, rode `.venv\Scripts\pip install -r requirements.txt`
e abra pelo `Parca.cmd`. As atualizações chegam do mesmo jeito.

---

## English

Ombri also speaks **English** (voices Lily, Ivanna and Hale): it understands
you and answers in English. Download
[Ombri-Setup.exe](https://github.com/vallanders-dev/game-companion-client/releases/latest/download/Ombri-Setup.exe)
and open it - nothing else to install. If Windows says "Windows protected your
PC", click **More info**, then **Run anyway**. In the window that opens, pick
**English** as the language, choose a voice
(**Listen** plays a sample), paste your tester token and click **Connect**.

- **F8**: ask by voice · **F6**: save a note (it asks you to confirm) ·
  press either while it talks to interrupt
- Say "thanks" or "got it" to wrap up; "tell me everything" unlocks spoilers
- If your game runs as administrator, click **Reopen as admin** on the
  notice Ombri shows, or right-click **Ombri** (Start menu or desktop) and
  choose **Run as administrator**
- Change language or voice any time with **Settings** in the Ombri window or tray
- Closing the Ombri window keeps it running in the Windows tray (near the
  clock): click the tray icon to reopen it, pause it or quit
