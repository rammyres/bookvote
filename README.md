# Enquete de Livros

Ferramenta para clubes de leitura, com dois módulos independentes, sem
contas de usuário. Feito em FastAPI + SQLite.

- **Enquete**: indicação de livros → votação múltipla (1ª fase) → votação
  única entre os finalistas (2ª fase) → campeão, com sorteio auditável em
  caso de empate.
- **Sorteio**: inscrição (nome + celular) durante um prazo → sorteio ao vivo
  com contagem regressiva, leitura dos nomes em voz alta e animação de
  roleta até revelar o(s) sorteado(s).

## Enquete

- **Indicações**: qualquer pessoa com o link sugere livros até o prazo
  definido. Busca em tempo real via Google Books (título, autor, ISBN e
  capa vêm prontos); também dá pra digitar livre, sem capa. Limite
  configurável de indicações por pessoa.
- **Revisão (congelada)**: ao encerrar as indicações, a lista trava e a
  votação **não** começa sozinha — o organizador recebe um e-mail e precisa
  entrar no painel, revisar (pode recusar indicações e reverter) e clicar
  em "Liberar para votação", escolhendo aí o prazo da 1ª votação.
- **Votação 1 (múltipla)**: todo livro indicado entra na enquete; cada
  visitante marca quantos quiser e pode trocar o voto até o prazo acabar.
- **Corte para a votação 2**: por padrão avançam os 3 mais votados, e um
  empate na última vaga é resolvido por sorteio (restrito só aos
  empatados). Ao criar a enquete dá pra escolher a política alternativa
  "todos os empatados avançam" — sem sorteio nesse corte, mas o top 3 pode
  virar 4, 5 finalistas.
- **Votação 2 (única)**: um voto por pessoa entre os finalistas.
- **Resultado**: mais votado na votação 2 vence. Empate em 1º é sempre
  resolvido por sorteio (nunca "todos avançam" — só pode haver um campeão),
  com animação restrita aos livros empatados e registro auditável
  (candidatos + seed + sorteado) na página pública.
- **Administração**: link secreto de admin (`/admin/<token>`) para encerrar
  fases antes do prazo e acionar sorteios. Se um e-mail for informado na
  criação, o link também chega por lá (Resend) e pode ser reenviado a
  qualquer momento pela página pública.

> **Enquetes antigas**: o esquema já teve versões incompatíveis entre si
> (3 prazos ao invés de 2, coluna de política de empate, etc.). Se você
> vem de uma versão bem antiga e a aplicação não subir, o caminho mais
> simples é apagar o volume e recriar:
>
> ```bash
> docker compose down
> docker volume rm bookvote_bookvote_data   # nome pode variar, veja `docker volume ls`
> docker compose up -d --build
> ```

## Sorteio

- **Cadastro**: título, descrição, prazo de inscrição e quantos sorteados
  (definido na criação, não muda depois — o sorteio escolhe essa
  quantidade de uma vez só).
- **Inscrição**: nome + celular, com captcha. Celular é a chave de
  deduplicação (normalizado, sem formatação) — uma pessoa não entra duas
  vezes na mesma rifa.
- **Painel do organizador** (`/admin/raffle/<token>`): lista de inscritos,
  botão para ler os nomes em voz alta (Web Speech API) e botão para
  realizar o sorteio — contagem regressiva, rolete de nomes desacelerando
  e anúncio falado do vencedor. O sorteio em si roda no servidor com RNG
  criptográfico antes da animação começar; a animação é só a apresentação
  em cima de um resultado já gravado e auditável (seed visível no painel).
- Depende do navegador ter voz em pt-BR instalada para falar em voz alta;
  sem isso a leitura visual/texto continua funcionando normalmente.

## Controle anti-bot (sem exigir conta)

1. Cookie assinado identifica o navegador.
2. Limite de identidades de votante por IP/enquete
   (`BOOKVOTE_MAX_VOTERS_PER_IP`, padrão 6) e de inscrições por IP/sorteio
   (`BOOKVOTE_MAX_RAFFLE_ENTRIES_PER_IP`, padrão 6).
3. **Log de votos append-only**: nenhum voto é apagado — cada cédula nova
   anula (sem excluir) os votos anteriores da mesma pessoa **ou** do mesmo
   IP naquela rodada; só a mais recente por IP conta na apuração. Isso
   significa que, num IP compartilhado (Wi-Fi de casa, evento), só o
   último voto daquele IP conta — troca deliberada de justiça em rede
   compartilhada por dificultar fraude. Pra público assim, autenticação
   real (e-mail ou Telegram) seria a proteção de verdade.
4. Captcha (Cloudflare Turnstile, gratuito) em indicação, voto e inscrição
   de sorteio.
5. Rate limiting por IP nas rotas de escrita.

Nenhuma camada é perfeita sozinha, mas juntas encarecem o abuso o
suficiente para esse porte de ferramenta.

**E-mail (Resend)**: sem `RESEND_API_KEY`, tudo funciona normalmente — só
não envia e-mail de administração. No plano gratuito sem domínio
verificado, o remetente de testes só entrega pro e-mail da sua própria
conta Resend; para enviar a qualquer pessoa, verifique um domínio em
https://resend.com/domains e troque `RESEND_FROM_EMAIL`.

**E-mails de mudança de fase** (indicações encerradas, empate, resultado
final) dependem de `BOOKVOTE_BASE_URL` (ex.:
`https://enquete.seudominio.com.br`, sem barra no final) — um verificador
roda em segundo plano a cada 2 minutos (`BOOKVOTE_NOTIFY_INTERVAL_SECONDS`)
e dispara esses e-mails assim que a fase muda, mesmo que ninguém abra a
enquete ou o painel de admin naquele momento. Sem essa variável, esses
e-mails só saem quando alguém efetivamente visita uma dessas páginas — o
que pode significar horas de atraso numa enquete com pouco tráfego. O
`setup_env.sh` já pergunta por ela.

## Rodar localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # gere BOOKVOTE_SECRET_KEY como indicado no arquivo
uvicorn app.main:app --reload
```

Abra http://localhost:8000 — sem as chaves do Turnstile, o captcha fica
desativado automaticamente. O `.env` é lido tanto local (`python-dotenv`)
quanto no Docker (`env_file`), sem precisar exportar nada manualmente.

**Busca de livros**: sem `GOOGLE_BOOKS_API_KEY`, usa a cota pública
anônima do Google Books (pequena, aparece como `429` no log sob uso
normal — a indicação manual continua funcionando). Depois de editar o
`.env`, confira no log se apareceu "API key carregada"; o `--reload` às
vezes não pega variáveis de ambiente novas entre reinícios do processo
pai — se persistir, pare e suba o `uvicorn` de novo.

## Deploy na Oracle Cloud (nginx já instalado na VM)

Pressupondo uma instância Compute (Ubuntu) com nginx já rodando e portas
80/443 liberadas na Security List/NSG do VCN e no firewall da instância.

1. **Envie os arquivos**: `scp -r bookvote/ ubuntu@SEU_IP:~/bookvote`
2. **Instale Docker** (se não tiver):

   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Confirme as portas 80/443** liberadas (provavelmente já estão, já que
   o nginx roda outros sites).
4. **Configure o `.env`** (gera a chave secreta automaticamente):

   ```bash
   cd ~/bookvote
   ./scripts/setup_env.sh
   ```

   Pergunta as chaves do Turnstile (https://dash.cloudflare.com/ →
   Turnstile — pode deixar em branco pra testar sem captcha), Google Books
   e Resend (opcionais) e o limite de votantes por IP.

   Não-interativo:

   ```bash
   ./scripts/setup_env.sh --yes \
     --turnstile-site SEU_SITE_KEY --turnstile-secret SEU_SECRET_KEY \
     --max-voters 8 --google-books-key SUA_CHAVE
   ```

   Rodar de novo não perde a chave secreta já gerada — faz backup do
   `.env` anterior e só atualiza o que for passado.
5. **Suba o container** (só a app, sem proxy próprio — fica só em
   `127.0.0.1:8000`, nunca exposto direto):

   ```bash
   docker compose up -d --build
   ```
6. **Aponte o nginx**:

   ```bash
   sudo cp deploy/nginx-bookvote.conf /etc/nginx/sites-available/bookvote
   sudo nano /etc/nginx/sites-available/bookvote   # ajuste o server_name
   sudo ln -s /etc/nginx/sites-available/bookvote /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```
7. **HTTPS** (se já usa certbot na VM, detecta o server block novo):

   ```bash
   sudo certbot --nginx -d enquete.seudominio.com.br
   ```
8. Acesse `https://enquete.seudominio.com.br` e crie a primeira enquete ou
   sorteio — guarde o link de admin exibido na criação.

> **Por que confiar em `X-Forwarded-For`**: o app usa esse cabeçalho para
> identificar IPs (limites por rede, rate limiting). O `docker-compose.yml`
> publica o container só em `127.0.0.1`, e o `Dockerfile` roda o uvicorn
> com `--proxy-headers --forwarded-allow-ips='*'` — seguro porque, pela
> topologia de rede, só o nginx do próprio host alcança a porta 8000. O
> `deploy/nginx-bookvote.conf` já envia esse cabeçalho; usando outro proxy,
> confirme que ele também envia `X-Forwarded-For` e `X-Forwarded-Proto`.

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
