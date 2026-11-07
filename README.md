# Enquete de Livros

Ferramenta para clubes de leitura: enquetes de livro e sorteios entre
participantes, sem contas de usuário. FastAPI + SQLite, pensada para rodar
numa única instância pequena (Docker + nginx na frente).

- **Enquete**: indicação de livros → votação múltipla → votação única
  entre os finalistas → campeão, com sorteio auditável em caso de empate.
- **Sorteio**: inscrição (nome + celular) durante um prazo → sorteio ao
  vivo com contagem regressiva, leitura dos nomes em voz alta e animação
  até revelar o(s) sorteado(s).

## Enquete

- **Indicações** (`/p/<id>`): qualquer pessoa com o link sugere livros até
  o prazo definido na criação. Busca em tempo real via Google Books
  (título, autor, ISBN e capa vêm prontos) ou indicação manual, sem capa.
  Limite configurável de indicações por pessoa.
- **Revisão (congelada)**: ao encerrar as indicações, a lista trava e a
  votação **não** começa sozinha — o organizador entra no painel
  (`/admin/<token>`), revisa (pode recusar indicações e reverter) e clica
  em "Liberar para votação", escolhendo aí o prazo da 1ª votação.
- **Votação 1 (múltipla)**: todo livro indicado entra na enquete; cada
  visitante marca quantos quiser e pode trocar o voto até o prazo acabar.
- **Corte para a votação 2**: escolhido na criação da enquete —
  por padrão avançam os 3 mais votados, com empate na última vaga
  resolvido por sorteio (restrito só aos empatados, disparado pelo
  organizador no painel). A alternativa "todos os empatados avançam"
  pula esse sorteio — o top 3 pode virar 4, 5 finalistas.
- **Votação 2 (única)**: um voto por pessoa entre os finalistas.
- **Resultado**: mais votado na votação 2 vence. Empate em 1º é sempre
  resolvido por sorteio (só pode haver um campeão), com animação e
  registro auditável (candidatos + seed + sorteado) na página pública.
- **Administração**: link secreto de admin para encerrar fases antes do
  prazo e acionar sorteios. Se um e-mail for informado na criação, o link
  chega por lá (Resend) e também pode ser recuperado a qualquer momento
  pela página pública ("perdeu o link de administração?").
- **E-mails automáticos de mudança de fase** (indicações encerradas,
  empates, resultado final) — ver seção de notificações mais abaixo.

## Sorteio

- **Cadastro** (`/raffles/new`): título, descrição, prazo de inscrição e
  quantos sorteados (definido na criação, não muda depois).
- **Inscrição** (`/r/<id>`): nome + celular, com captcha. Celular é a
  chave de deduplicação (normalizado, sem formatação) — uma pessoa não
  entra duas vezes na mesma rifa. A página também mostra a lista de
  inscritos até então, com o celular parcialmente mascarado.
- **Painel do organizador** (`/admin/raffle/<token>`):
  - lista completa de inscritos;
  - botão para ler os nomes em voz alta (Web Speech API, com controle de
    velocidade de leitura);
  - **inclusão manual de inscritos** — só liberada depois que o prazo de
    inscrição encerra e antes do sorteio rodar, para incluir alguém que
    confirmou por fora (WhatsApp, telefone) e não usou o formulário
    público a tempo;
  - botão para realizar o sorteio: contagem regressiva, rolete de nomes
    desacelerando e anúncio falado do(s) vencedor(es). O sorteio em si
    roda no servidor com RNG criptográfico antes da animação começar — a
    animação é só a apresentação em cima de um resultado já gravado e
    auditável (seed visível no painel);
  - depois do sorteio, o telefone de cada vencedor vira um link direto
    pro WhatsApp (`wa.me`) pra facilitar o contato.
- Leitura em voz alta depende do navegador ter voz em pt-BR instalada;
  sem isso, a leitura visual/texto continua funcionando normalmente.

## Leituras coletivas (LC)

Vitrine de leituras coletivas do clube, com as **capas em destaque**.

- **Home**: no topo, a seção "Leituras coletivas em andamento" (só capas,
  clicáveis), as "Em breve" (previstas) logo abaixo e o botão
  "Leituras já concluídas" (`/lc/concluidas`). Também há `/lc` (andamento +
  próximas) e `/lc/<id>` (capa grande, cronograma e descrição).
- **Estados**: *prevista*, *em andamento* e *concluída*. Prevista vira
  em andamento **sozinha** quando a data de início chega (virada à meia-noite
  do fuso `BOOKVOTE_TZ`, padrão `America/Sao_Paulo`; vale na próxima página
  que alguém abrir, sem cron). Concluir é sempre manual, pelo painel.
  Concluídas não precisam de cronograma.
- **Cronograma**: data de início, data de fim e etapas em texto livre. A
  data de início é obrigatória para *em andamento*; uma leitura *prevista*
  pode ficar **sem data** ("data a definir") e não vira em andamento
  sozinha — o admin usa o botão "Iniciar agora" no painel (o início passa a
  ser a data de hoje).
- **Categorias**: o **administrador principal** cadastra previamente as
  categorias em `/adminlc/categorias` (criar, renomear, apagar — apagar solta
  as leituras, que ficam "sem categoria"). Ao cadastrar/editar uma leitura,
  qualquer administrador escolhe uma delas numa lista (ou nenhuma).
- **Agrupamento**: no painel (`/adminlc`) o administrador principal escolhe
  o modo — sem agrupamento, **por categoria** ou **por autor** — e **em quais
  páginas** vale: página principal (home e `/lc`), leituras concluídas
  (`/lc/concluidas`), as duas ou nenhuma. Grupos em ordem alfabética,
  "Sem categoria"/"Sem autor" por último. Sem configuração salva, o
  agrupamento (quando ligado) vale nas duas. O banco ganha a coluna
  `category_id` e as tabelas `lc_categories` e `site_settings` sozinhos na
  primeira subida (sem migração manual).
- **Destaque na home**: leitura *em andamento* cuja categoria se chama
  **"Leitura principal"** (sem diferença de maiúsculas/acentos) aparece num
  bloco grande no topo da seção de andamento, com capa, autor, período e
  trecho da descrição, e sai da grade abaixo. Basta criar a categoria com
  esse nome em `/adminlc/categorias` (renomeá-la desliga o destaque).
- **Capa**: busca no Google Books (preenche título, autor, ISBN e capa) ou
  upload de JPG/PNG/WEBP/GIF até 5 MB. A capa do Google é **baixada para o
  servidor** (`/data/lc_covers`, mesmo volume do banco) — se o download
  falhar, o app usa o link do Google como reserva. Se o admin enviar um
  arquivo, ele tem prioridade.
- **Administração** (`/adminlc`): login com usuário e senha (mín. 10
  caracteres). O **administrador principal** cria/desativa outros
  administradores e redefine a senha deles; todos os administradores
  cadastram, editam, concluem e apagam leituras.

Primeiro acesso — crie o administrador principal no servidor (pede a senha
sem ecoar; rodar de novo com o mesmo usuário redefine a senha dele):

```bash
docker compose exec web python scripts/manage.py lc-main-admin SEU_USUARIO
docker compose exec web python scripts/manage.py list-lc-admins
```

Upload de capa atrás do nginx precisa de `client_max_body_size` (já no
`deploy/nginx-bookvote.conf` de exemplo; se você já copiou o arquivo antes,
adicione `client_max_body_size 6m;` no `server` e recarregue o nginx).

## Notificações automáticas de mudança de fase

As enquetes disparam e-mail pro organizador quando: as indicações
encerram (pedindo revisão), há empate pendente de sorteio (1ª→2ª votação
ou campeão), e quando o resultado final sai. Isso é checado de duas
formas, que se complementam:

1. **Na hora**, se alguém abrir a página da enquete ou o painel de admin
   justamente quando a fase muda.
2. **Por cron**, rodando `scripts/notify.py` periodicamente — faz uma
   varredura em todas as enquetes pendentes e dispara o que for devido,
   independente de qualquer visita. Sem isso agendado, só vale a checagem
   "na hora" do item 1, o que numa enquete com pouco tráfego pode
   significar horas de atraso entre a fase mudar e o e-mail sair.

   Precisa de `BOOKVOTE_BASE_URL` configurada (ex.:
   `https://enquete.seudominio.com.br`, sem barra no final —
   `setup_env.sh` já pergunta por essa URL); sem ela, o script só avisa
   no log e não envia nada.

   Agendamento (no crontab do **host**, não dentro do container — cron
   chama o `docker compose exec` de fora):

   ```bash
   crontab -e
   ```

   ```cron
   */2 * * * * cd /caminho/do/bookvote && docker compose exec -T web python scripts/notify.py >> /var/log/bookvote-notify.log 2>&1
   ```

   O `-T` desliga alocação de pseudo-TTY — sem ele, `docker compose exec`
   falha rodando via cron (só funciona interativo, no terminal).

   Reenvio automático: se o Resend falhar numa execução (rede instável,
   API fora do ar), a notificação fica pendente e é tentada de novo na
   próxima execução do cron — não fica marcada como enviada até o envio
   realmente funcionar.

## Controle anti-bot (sem exigir conta)

1. Cookie assinado identifica o navegador.
2. Limite de identidades de votante por IP/enquete
   (`BOOKVOTE_MAX_VOTERS_PER_IP`, padrão 6) e de inscrições por IP/sorteio
   (`BOOKVOTE_MAX_RAFFLE_ENTRIES_PER_IP`, padrão 6).
3. **Log de votos append-only**: nenhum voto é apagado — cada cédula nova
   anula (sem excluir) os votos anteriores da mesma pessoa **ou** do mesmo
   IP naquela rodada; só a mais recente por IP conta na apuração. Num IP
   compartilhado (Wi-Fi de casa, evento), isso significa que só o último
   voto daquele IP conta — troca deliberada de justiça em rede
   compartilhada por dificultar fraude. Pra público assim, autenticação
   real (e-mail ou Telegram) seria a proteção de verdade.
4. Captcha (Cloudflare Turnstile, gratuito) em indicação, voto e inscrição
   de sorteio.
5. Rate limiting por IP nas rotas de escrita.

Nenhuma camada é perfeita sozinha, mas juntas encarecem o abuso o
suficiente para esse porte de ferramenta.

## Testando localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # gere BOOKVOTE_SECRET_KEY como indicado no arquivo
uvicorn app.main:app --reload
```

Abra http://localhost:8000. Sem as chaves do Turnstile, o captcha fica
desativado automaticamente — dá pra testar o fluxo inteiro (indicação,
votação, sorteio) sem precisar resolver captcha manualmente. Sem
`BOOKVOTE_BASE_URL`, os e-mails de mudança de fase só disparam ao abrir a
página na hora certa (ver seção acima) — para testar a varredura por
cron, defina `BOOKVOTE_BASE_URL=http://localhost:8000` no `.env` e rode
`python scripts/notify.py` manualmente (sem precisar agendar nada).

Sem `RESEND_API_KEY`, nenhum e-mail é enviado de verdade — o app só
registra no log o que teria sido enviado, então dá pra testar o fluxo de
criação/recuperação de link sem precisar de conta no Resend.

**Busca de livros**: sem `GOOGLE_BOOKS_API_KEY`, usa a cota pública
anônima do Google Books (pequena, aparece como `429` no log sob uso
normal — a indicação manual continua funcionando).

## Deploy (Docker + nginx já instalado na VM)

Pressupondo uma instância (Ubuntu) com nginx já rodando e portas 80/443
liberadas no firewall.

1. **Envie os arquivos**: `scp -r bookvote/ ubuntu@SEU_IP:~/bookvote`
2. **Instale Docker** (se não tiver):

   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Configure o `.env`** (gera a chave secreta automaticamente):

   ```bash
   cd ~/bookvote
   ./scripts/setup_env.sh
   ```

   Pergunta as chaves do Turnstile (https://dash.cloudflare.com/ →
   Turnstile — pode deixar em branco pra testar sem captcha), Google
   Books e Resend (opcionais), o limite de votantes por IP e a URL
   pública da instância.

   Não-interativo:

   ```bash
   ./scripts/setup_env.sh --yes \
     --turnstile-site SEU_SITE_KEY --turnstile-secret SEU_SECRET_KEY \
     --max-voters 8 --google-books-key SUA_CHAVE \
     --base-url https://enquete.seudominio.com.br
   ```

   Rodar de novo não perde a chave secreta já gerada — faz backup do
   `.env` anterior e só atualiza o que for passado.
4. **Suba o container** (só a app, sem proxy próprio — fica só em
   `127.0.0.1:8000`, nunca exposto direto):

   ```bash
   docker compose up -d --build
   ```

   O serviço do compose se chama `web` — use esse nome em qualquer
   `docker compose exec`/`logs`/`restart`.
5. **Aponte o nginx**:

   ```bash
   sudo cp deploy/nginx-bookvote.conf /etc/nginx/sites-available/bookvote
   sudo nano /etc/nginx/sites-available/bookvote   # ajuste o server_name
   sudo ln -s /etc/nginx/sites-available/bookvote /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```
6. **HTTPS** (se já usa certbot na VM, detecta o server block novo):

   ```bash
   sudo certbot --nginx -d enquete.seudominio.com.br
   ```
7. Acesse `https://enquete.seudominio.com.br` e crie a primeira enquete ou
   sorteio — guarde o link de admin exibido na criação.

> **Por que confiar em `X-Forwarded-For`**: o app usa esse cabeçalho para
> identificar IPs (limites por rede, rate limiting). O `docker-compose.yml`
> publica o container só em `127.0.0.1`, e o `Dockerfile` roda o uvicorn
> com `--proxy-headers --forwarded-allow-ips='*'` — seguro porque, pela
> topologia de rede, só o nginx do próprio host alcança a porta 8000. O
> `deploy/nginx-bookvote.conf` já envia esse cabeçalho; usando outro proxy,
> confirme que ele também envia `X-Forwarded-For` e `X-Forwarded-Proto`.

### Administração de sistema (apagar enquete/sorteio de teste)

`scripts/manage.py` lista e apaga enquetes/sorteios direto no banco — não
é exposto na web, só roda com acesso ao servidor. Serve pra remover algo
criado por engano ou só pra testar:

```bash
docker compose exec web python scripts/manage.py list-polls
docker compose exec web python scripts/manage.py list-raffles
docker compose exec web python scripts/manage.py delete-poll <id>
docker compose exec web python scripts/manage.py delete-raffle <id>
```

Apagar pede confirmação (digitar o ID de novo) e é irreversível — some a
enquete/sorteio e tudo que pertence a ela (livros, votos, inscritos,
sorteios de desempate). `--yes` pula a confirmação interativa.

### Backup

Dados ficam no volume Docker `bookvote_data` (SQLite):

```bash
docker run --rm -v bookvote_data:/data -v $PWD:/backup alpine \
  cp /data/bookvote.db /backup/bookvote-backup.db
# capas das leituras coletivas (se houver):
docker run --rm -v bookvote_data:/data -v $PWD:/backup alpine \
  tar czf /backup/lc_covers-backup.tgz -C /data lc_covers
```

### Atualizando depois de mudar o código

```bash
docker compose up -d --build
```

# Enquete de Livros

Ferramenta para clubes de leitura: enquetes de livro e sorteios entre
participantes, sem contas de usuário. FastAPI + SQLite, pensada para rodar
numa única instância pequena (Docker + nginx na frente).

- **Enquete**: indicação de livros → votação múltipla → votação única
  entre os finalistas → campeão, com sorteio auditável em caso de empate.
- **Sorteio**: inscrição (nome + celular) durante um prazo → sorteio ao
  vivo com contagem regressiva, leitura dos nomes em voz alta e animação
  até revelar o(s) sorteado(s).

## Enquete

- **Indicações** (`/p/<id>`): qualquer pessoa com o link sugere livros até
  o prazo definido na criação. Busca em tempo real via Google Books
  (título, autor, ISBN e capa vêm prontos) ou indicação manual, sem capa.
  Limite configurável de indicações por pessoa.
- **Revisão (congelada)**: ao encerrar as indicações, a lista trava e a
  votação **não** começa sozinha — o organizador entra no painel
  (`/admin/<token>`), revisa (pode recusar indicações e reverter) e clica
  em "Liberar para votação", escolhendo aí o prazo da 1ª votação.
- **Votação 1 (múltipla)**: todo livro indicado entra na enquete; cada
  visitante marca quantos quiser e pode trocar o voto até o prazo acabar.
- **Corte para a votação 2**: escolhido na criação da enquete —
  por padrão avançam os 3 mais votados, com empate na última vaga
  resolvido por sorteio (restrito só aos empatados, disparado pelo
  organizador no painel). A alternativa "todos os empatados avançam"
  pula esse sorteio — o top 3 pode virar 4, 5 finalistas.
- **Votação 2 (única)**: um voto por pessoa entre os finalistas.
- **Resultado**: mais votado na votação 2 vence. Empate em 1º é sempre
  resolvido por sorteio (só pode haver um campeão), com animação e
  registro auditável (candidatos + seed + sorteado) na página pública.
- **Administração**: link secreto de admin para encerrar fases antes do
  prazo e acionar sorteios. Se um e-mail for informado na criação, o link
  chega por lá (Resend) e também pode ser recuperado a qualquer momento
  pela página pública ("perdeu o link de administração?").
- **E-mails automáticos de mudança de fase** (indicações encerradas,
  empates, resultado final) — ver seção de notificações mais abaixo.

## Sorteio

- **Cadastro** (`/raffles/new`): título, descrição, prazo de inscrição e
  quantos sorteados (definido na criação, não muda depois).
- **Inscrição** (`/r/<id>`): nome + celular, com captcha. Celular é a
  chave de deduplicação (normalizado, sem formatação) — uma pessoa não
  entra duas vezes na mesma rifa. A página também mostra a lista de
  inscritos até então, com o celular parcialmente mascarado.
- **Painel do organizador** (`/admin/raffle/<token>`):
  - lista completa de inscritos;
  - botão para ler os nomes em voz alta (Web Speech API, com controle de
    velocidade de leitura);
  - **inclusão manual de inscritos** — só liberada depois que o prazo de
    inscrição encerra e antes do sorteio rodar, para incluir alguém que
    confirmou por fora (WhatsApp, telefone) e não usou o formulário
    público a tempo;
  - botão para realizar o sorteio: contagem regressiva, rolete de nomes
    desacelerando e anúncio falado do(s) vencedor(es). O sorteio em si
    roda no servidor com RNG criptográfico antes da animação começar — a
    animação é só a apresentação em cima de um resultado já gravado e
    auditável (seed visível no painel);
  - depois do sorteio, o telefone de cada vencedor vira um link direto
    pro WhatsApp (`wa.me`) pra facilitar o contato.
- Leitura em voz alta depende do navegador ter voz em pt-BR instalada;
  sem isso, a leitura visual/texto continua funcionando normalmente.

## Notificações automáticas de mudança de fase

As enquetes disparam e-mail pro organizador quando: as indicações
encerram (pedindo revisão), há empate pendente de sorteio (1ª→2ª votação
ou campeão), e quando o resultado final sai. Isso é checado de duas
formas, que se complementam:

1. **Na hora**, se alguém abrir a página da enquete ou o painel de admin
   justamente quando a fase muda.
2. **Por cron**, rodando `scripts/notify.py` periodicamente — faz uma
   varredura em todas as enquetes pendentes e dispara o que for devido,
   independente de qualquer visita. Sem isso agendado, só vale a checagem
   "na hora" do item 1, o que numa enquete com pouco tráfego pode
   significar horas de atraso entre a fase mudar e o e-mail sair.

   Precisa de `BOOKVOTE_BASE_URL` configurada (ex.:
   `https://enquete.seudominio.com.br`, sem barra no final —
   `setup_env.sh` já pergunta por essa URL); sem ela, o script só avisa
   no log e não envia nada.

   Agendamento (no crontab do **host**, não dentro do container — cron
   chama o `docker compose exec` de fora):

   ```bash
   crontab -e
   ```

   ```cron
   */2 * * * * cd /caminho/do/bookvote && docker compose exec -T web python scripts/notify.py >> /var/log/bookvote-notify.log 2>&1
   ```

   O `-T` desliga alocação de pseudo-TTY — sem ele, `docker compose exec`
   falha rodando via cron (só funciona interativo, no terminal).

   Reenvio automático: se o Resend falhar numa execução (rede instável,
   API fora do ar), a notificação fica pendente e é tentada de novo na
   próxima execução do cron — não fica marcada como enviada até o envio
   realmente funcionar.

## Controle anti-bot (sem exigir conta)

1. Cookie assinado identifica o navegador.
2. Limite de identidades de votante por IP/enquete
   (`BOOKVOTE_MAX_VOTERS_PER_IP`, padrão 6) e de inscrições por IP/sorteio
   (`BOOKVOTE_MAX_RAFFLE_ENTRIES_PER_IP`, padrão 6).
3. **Log de votos append-only**: nenhum voto é apagado — cada cédula nova
   anula (sem excluir) os votos anteriores da mesma pessoa **ou** do mesmo
   IP naquela rodada; só a mais recente por IP conta na apuração. Num IP
   compartilhado (Wi-Fi de casa, evento), isso significa que só o último
   voto daquele IP conta — troca deliberada de justiça em rede
   compartilhada por dificultar fraude. Pra público assim, autenticação
   real (e-mail ou Telegram) seria a proteção de verdade.
4. Captcha (Cloudflare Turnstile, gratuito) em indicação, voto e inscrição
   de sorteio.
5. Rate limiting por IP nas rotas de escrita.

Nenhuma camada é perfeita sozinha, mas juntas encarecem o abuso o
suficiente para esse porte de ferramenta.

## Testando localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # gere BOOKVOTE_SECRET_KEY como indicado no arquivo
uvicorn app.main:app --reload
```

Abra http://localhost:8000. Sem as chaves do Turnstile, o captcha fica
desativado automaticamente — dá pra testar o fluxo inteiro (indicação,
votação, sorteio) sem precisar resolver captcha manualmente. Sem
`BOOKVOTE_BASE_URL`, os e-mails de mudança de fase só disparam ao abrir a
página na hora certa (ver seção acima) — para testar a varredura por
cron, defina `BOOKVOTE_BASE_URL=http://localhost:8000` no `.env` e rode
`python scripts/notify.py` manualmente (sem precisar agendar nada).

Sem `RESEND_API_KEY`, nenhum e-mail é enviado de verdade — o app só
registra no log o que teria sido enviado, então dá pra testar o fluxo de
criação/recuperação de link sem precisar de conta no Resend.

**Busca de livros**: sem `GOOGLE_BOOKS_API_KEY`, usa a cota pública
anônima do Google Books (pequena, aparece como `429` no log sob uso
normal — a indicação manual continua funcionando).

## Deploy (Docker + nginx já instalado na VM)

Pressupondo uma instância (Ubuntu) com nginx já rodando e portas 80/443
liberadas no firewall.

1. **Envie os arquivos**: `scp -r bookvote/ ubuntu@SEU_IP:~/bookvote`
2. **Instale Docker** (se não tiver):

   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Configure o `.env`** (gera a chave secreta automaticamente):

   ```bash
   cd ~/bookvote
   ./scripts/setup_env.sh
   ```

   Pergunta as chaves do Turnstile (https://dash.cloudflare.com/ →
   Turnstile — pode deixar em branco pra testar sem captcha), Google
   Books e Resend (opcionais), o limite de votantes por IP e a URL
   pública da instância.

   Não-interativo:

   ```bash
   ./scripts/setup_env.sh --yes \
     --turnstile-site SEU_SITE_KEY --turnstile-secret SEU_SECRET_KEY \
     --max-voters 8 --google-books-key SUA_CHAVE \
     --base-url https://enquete.seudominio.com.br
   ```

   Rodar de novo não perde a chave secreta já gerada — faz backup do
   `.env` anterior e só atualiza o que for passado.
4. **Suba o container** (só a app, sem proxy próprio — fica só em
   `127.0.0.1:8000`, nunca exposto direto):

   ```bash
   docker compose up -d --build
   ```

   O serviço do compose se chama `web` — use esse nome em qualquer
   `docker compose exec`/`logs`/`restart`.
5. **Aponte o nginx**:

   ```bash
   sudo cp deploy/nginx-bookvote.conf /etc/nginx/sites-available/bookvote
   sudo nano /etc/nginx/sites-available/bookvote   # ajuste o server_name
   sudo ln -s /etc/nginx/sites-available/bookvote /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```
6. **HTTPS** (se já usa certbot na VM, detecta o server block novo):

   ```bash
   sudo certbot --nginx -d enquete.seudominio.com.br
   ```
7. Acesse `https://enquete.seudominio.com.br` e crie a primeira enquete ou
   sorteio — guarde o link de admin exibido na criação.

> **Por que confiar em `X-Forwarded-For`**: o app usa esse cabeçalho para
> identificar IPs (limites por rede, rate limiting). O `docker-compose.yml`
> publica o container só em `127.0.0.1`, e o `Dockerfile` roda o uvicorn
> com `--proxy-headers --forwarded-allow-ips='*'` — seguro porque, pela
> topologia de rede, só o nginx do próprio host alcança a porta 8000. O
> `deploy/nginx-bookvote.conf` já envia esse cabeçalho; usando outro proxy,
> confirme que ele também envia `X-Forwarded-For` e `X-Forwarded-Proto`.

### Administração de sistema (apagar enquete/sorteio de teste)

`scripts/manage.py` lista e apaga enquetes/sorteios direto no banco — não
é exposto na web, só roda com acesso ao servidor. Serve pra remover algo
criado por engano ou só pra testar:

```bash
docker compose exec web python scripts/manage.py list-polls
docker compose exec web python scripts/manage.py list-raffles
docker compose exec web python scripts/manage.py delete-poll <id>
docker compose exec web python scripts/manage.py delete-raffle <id>
```

Apagar pede confirmação (digitar o ID de novo) e é irreversível — some a
enquete/sorteio e tudo que pertence a ela (livros, votos, inscritos,
sorteios de desempate). `--yes` pula a confirmação interativa.

### Backup

Dados ficam no volume Docker `bookvote_data` (SQLite):

```bash
docker run --rm -v bookvote_data:/data -v $PWD:/backup alpine \
  cp /data/bookvote.db /backup/bookvote-backup.db
```

### Atualizando depois de mudar o código

```bash
docker compose up -d --build
```

# Enquete de Livros

Ferramenta para clubes de leitura: enquetes de livro e sorteios entre
participantes, sem contas de usuário. FastAPI + SQLite, pensada para rodar
numa única instância pequena (Docker + nginx na frente).

- **Enquete**: indicação de livros → votação múltipla → votação única
  entre os finalistas → campeão, com sorteio auditável em caso de empate.
- **Sorteio**: inscrição (nome + celular) durante um prazo → sorteio ao
  vivo com contagem regressiva, leitura dos nomes em voz alta e animação
  até revelar o(s) sorteado(s).

## Enquete

- **Indicações** (`/p/<id>`): qualquer pessoa com o link sugere livros até
  o prazo definido na criação. Busca em tempo real via Google Books
  (título, autor, ISBN e capa vêm prontos) ou indicação manual, sem capa.
  Limite configurável de indicações por pessoa.
- **Revisão (congelada)**: ao encerrar as indicações, a lista trava e a
  votação **não** começa sozinha — o organizador entra no painel
  (`/admin/<token>`), revisa (pode recusar indicações e reverter) e clica
  em "Liberar para votação", escolhendo aí o prazo da 1ª votação.
- **Votação 1 (múltipla)**: todo livro indicado entra na enquete; cada
  visitante marca quantos quiser e pode trocar o voto até o prazo acabar.
- **Corte para a votação 2**: escolhido na criação da enquete —
  por padrão avançam os 3 mais votados, com empate na última vaga
  resolvido por sorteio (restrito só aos empatados, disparado pelo
  organizador no painel). A alternativa "todos os empatados avançam"
  pula esse sorteio — o top 3 pode virar 4, 5 finalistas.
- **Votação 2 (única)**: um voto por pessoa entre os finalistas.
- **Resultado**: mais votado na votação 2 vence. Empate em 1º é sempre
  resolvido por sorteio (só pode haver um campeão), com animação e
  registro auditável (candidatos + seed + sorteado) na página pública.
- **Administração**: link secreto de admin para encerrar fases antes do
  prazo e acionar sorteios. Se um e-mail for informado na criação, o link
  chega por lá (Resend) e também pode ser recuperado a qualquer momento
  pela página pública ("perdeu o link de administração?").
- **E-mails automáticos de mudança de fase** (indicações encerradas,
  empates, resultado final) — ver seção de notificações mais abaixo.

## Sorteio

- **Cadastro** (`/raffles/new`): título, descrição, prazo de inscrição e
  quantos sorteados (definido na criação, não muda depois).
- **Inscrição** (`/r/<id>`): nome + celular, com captcha. Celular é a
  chave de deduplicação (normalizado, sem formatação) — uma pessoa não
  entra duas vezes na mesma rifa. A página também mostra a lista de
  inscritos até então, com o celular parcialmente mascarado.
- **Painel do organizador** (`/admin/raffle/<token>`):
  - lista completa de inscritos;
  - botão para ler os nomes em voz alta (Web Speech API, com controle de
    velocidade de leitura);
  - **inclusão manual de inscritos** — só liberada depois que o prazo de
    inscrição encerra e antes do sorteio rodar, para incluir alguém que
    confirmou por fora (WhatsApp, telefone) e não usou o formulário
    público a tempo;
  - botão para realizar o sorteio: contagem regressiva, rolete de nomes
    desacelerando e anúncio falado do(s) vencedor(es). O sorteio em si
    roda no servidor com RNG criptográfico antes da animação começar — a
    animação é só a apresentação em cima de um resultado já gravado e
    auditável (seed visível no painel);
  - depois do sorteio, o telefone de cada vencedor vira um link direto
    pro WhatsApp (`wa.me`) pra facilitar o contato.
- Leitura em voz alta depende do navegador ter voz em pt-BR instalada;
  sem isso, a leitura visual/texto continua funcionando normalmente.

## Notificações automáticas de mudança de fase

As enquetes disparam e-mail pro organizador quando: as indicações
encerram (pedindo revisão), há empate pendente de sorteio (1ª→2ª votação
ou campeão), e quando o resultado final sai. Isso é checado de duas
formas, que se complementam:

1. **Na hora**, se alguém abrir a página da enquete ou o painel de admin
   justamente quando a fase muda.
2. **Em segundo plano**, por um verificador que roda dentro do próprio
   processo a cada `BOOKVOTE_NOTIFY_INTERVAL_SECONDS` (padrão 120s),
   independente de qualquer visita. Esse verificador só funciona se
   `BOOKVOTE_BASE_URL` estiver configurada (ex.:
   `https://enquete.seudominio.com.br`, sem barra no final) — sem ela, só
   vale a checagem "na hora" do item 1, o que numa enquete com pouco
   tráfego pode significar horas de atraso entre a fase mudar e o e-mail
   sair. `setup_env.sh` já pergunta por essa URL.

## Controle anti-bot (sem exigir conta)

1. Cookie assinado identifica o navegador.
2. Limite de identidades de votante por IP/enquete
   (`BOOKVOTE_MAX_VOTERS_PER_IP`, padrão 6) e de inscrições por IP/sorteio
   (`BOOKVOTE_MAX_RAFFLE_ENTRIES_PER_IP`, padrão 6).
3. **Log de votos append-only**: nenhum voto é apagado — cada cédula nova
   anula (sem excluir) os votos anteriores da mesma pessoa **ou** do mesmo
   IP naquela rodada; só a mais recente por IP conta na apuração. Num IP
   compartilhado (Wi-Fi de casa, evento), isso significa que só o último
   voto daquele IP conta — troca deliberada de justiça em rede
   compartilhada por dificultar fraude. Pra público assim, autenticação
   real (e-mail ou Telegram) seria a proteção de verdade.
4. Captcha (Cloudflare Turnstile, gratuito) em indicação, voto e inscrição
   de sorteio.
5. Rate limiting por IP nas rotas de escrita.

Nenhuma camada é perfeita sozinha, mas juntas encarecem o abuso o
suficiente para esse porte de ferramenta.

## Testando localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # gere BOOKVOTE_SECRET_KEY como indicado no arquivo
uvicorn app.main:app --reload
```

Abra http://localhost:8000. Sem as chaves do Turnstile, o captcha fica
desativado automaticamente — dá pra testar o fluxo inteiro (indicação,
votação, sorteio) sem precisar resolver captcha manualmente. Sem
`BOOKVOTE_BASE_URL`, os e-mails de mudança de fase só disparam ao abrir a
página na hora certa (ver seção acima) — para testar o verificador em
segundo plano, defina `BOOKVOTE_BASE_URL=http://localhost:8000` no `.env`.

Sem `RESEND_API_KEY`, nenhum e-mail é enviado de verdade — o app só
registra no log o que teria sido enviado, então dá pra testar o fluxo de
criação/recuperação de link sem precisar de conta no Resend.

**Busca de livros**: sem `GOOGLE_BOOKS_API_KEY`, usa a cota pública
anônima do Google Books (pequena, aparece como `429` no log sob uso
normal — a indicação manual continua funcionando).

## Deploy (Docker + nginx já instalado na VM)

Pressupondo uma instância (Ubuntu) com nginx já rodando e portas 80/443
liberadas no firewall.

1. **Envie os arquivos**: `scp -r bookvote/ ubuntu@SEU_IP:~/bookvote`
2. **Instale Docker** (se não tiver):

   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Configure o `.env`** (gera a chave secreta automaticamente):

   ```bash
   cd ~/bookvote
   ./scripts/setup_env.sh
   ```

   Pergunta as chaves do Turnstile (https://dash.cloudflare.com/ →
   Turnstile — pode deixar em branco pra testar sem captcha), Google
   Books e Resend (opcionais), o limite de votantes por IP e a URL
   pública da instância.

   Não-interativo:

   ```bash
   ./scripts/setup_env.sh --yes \
     --turnstile-site SEU_SITE_KEY --turnstile-secret SEU_SECRET_KEY \
     --max-voters 8 --google-books-key SUA_CHAVE \
     --base-url https://enquete.seudominio.com.br
   ```

   Rodar de novo não perde a chave secreta já gerada — faz backup do
   `.env` anterior e só atualiza o que for passado.
4. **Suba o container** (só a app, sem proxy próprio — fica só em
   `127.0.0.1:8000`, nunca exposto direto):

   ```bash
   docker compose up -d --build
   ```

   O serviço do compose se chama `web` — use esse nome em qualquer
   `docker compose exec`/`logs`/`restart`.
5. **Aponte o nginx**:

   ```bash
   sudo cp deploy/nginx-bookvote.conf /etc/nginx/sites-available/bookvote
   sudo nano /etc/nginx/sites-available/bookvote   # ajuste o server_name
   sudo ln -s /etc/nginx/sites-available/bookvote /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```
6. **HTTPS** (se já usa certbot na VM, detecta o server block novo):

   ```bash
   sudo certbot --nginx -d enquete.seudominio.com.br
   ```
7. Acesse `https://enquete.seudominio.com.br` e crie a primeira enquete ou
   sorteio — guarde o link de admin exibido na criação.

> **Por que confiar em `X-Forwarded-For`**: o app usa esse cabeçalho para
> identificar IPs (limites por rede, rate limiting). O `docker-compose.yml`
> publica o container só em `127.0.0.1`, e o `Dockerfile` roda o uvicorn
> com `--proxy-headers --forwarded-allow-ips='*'` — seguro porque, pela
> topologia de rede, só o nginx do próprio host alcança a porta 8000. O
> `deploy/nginx-bookvote.conf` já envia esse cabeçalho; usando outro proxy,
> confirme que ele também envia `X-Forwarded-For` e `X-Forwarded-Proto`.

### Administração de sistema (apagar enquete/sorteio de teste)

`scripts/manage.py` lista e apaga enquetes/sorteios direto no banco — não
é exposto na web, só roda com acesso ao servidor. Serve pra remover algo
criado por engano ou só pra testar:

```bash
docker compose exec web python scripts/manage.py list-polls
docker compose exec web python scripts/manage.py list-raffles
docker compose exec web python scripts/manage.py delete-poll <id>
docker compose exec web python scripts/manage.py delete-raffle <id>
```

Apagar pede confirmação (digitar o ID de novo) e é irreversível — some a
enquete/sorteio e tudo que pertence a ela (livros, votos, inscritos,
sorteios de desempate). `--yes` pula a confirmação interativa.

### Backup

Dados ficam no volume Docker `bookvote_data` (SQLite):

```bash
docker run --rm -v bookvote_data:/data -v $PWD:/backup alpine \
  cp /data/bookvote.db /backup/bookvote-backup.db
```

### Atualizando depois de mudar o código

```bash
docker compose up -d --build
```
