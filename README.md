# Enquete de Livros

Ferramenta pequena para clubes de leitura: indicação de livros → votação
múltipla (1ª fase) → votação única entre os 3 finalistas (2ª fase) →
campeão, com sorteio auditável em caso de empate. Feito em FastAPI + SQLite,
sem contas de usuário.

## Como funciona

- **Indicações**: qualquer pessoa com o link público sugere livros até o
  horário definido pelo organizador. Em vez de campos separados de título,
  autor e ISBN, a pessoa digita num único campo e a página sugere livros
  em tempo real usando o Google Books (título, autor, ISBN e capa vêm
  prontos ao clicar numa sugestão). Não achou o livro? Pode digitar
  qualquer texto e indicar assim mesmo, sem capa. Cada pessoa tem um limite
  configurável de indicações.
- **Revisão (congelada)**: ao encerrar o prazo de indicações, a lista trava
  automaticamente e a votação **não** começa sozinha — o organizador recebe
  um e-mail avisando e precisa entrar no painel para revisar. Nessa fase
  ainda dá pra recusar indicações (e reverter); só quando o organizador
  clica em "Liberar para votação" (escolhendo o prazo da 1ª votação nesse
  momento) é que ela realmente começa.
- **Votação 1 (múltipla)**: a lista trava e todos os livros indicados entram
  na enquete, exibidos com capa (quando disponível) + nome. Cada visitante
  marca quantos livros quiser e pode voltar para trocar o voto até o prazo
  acabar.
- **Votação 2 (única)**: ao encerrar a votação 1, os 3 mais votados avançam
  — se houver empate na 3ª vaga, **todos** os empatados avançam (pode virar
  4, 5 finalistas, etc.). Nessa fase cada pessoa vota em só 1 livro entre os
  finalistas.
- **Resultado**: o mais votado na votação 2 é o campeão. Se houver empate em
  1º lugar, o organizador aciona o sorteio pelo painel — uma animação de
  roleta gira só entre os livros empatados e termina com um "selo" no
  vencedor real — restrito aos livros empatados (nunca a lista inteira). O
  sorteio fica registrado (candidatos + seed + sorteado) para qualquer
  pessoa conferir na página pública. Sem JavaScript, o mesmo botão ainda
  funciona (só sem a animação).
- **Administração**: quem cria a enquete recebe um link secreto de admin
  (`/admin/<token>`) para encerrar fases antes do prazo e acionar o sorteio.
  Se informar um e-mail ao criar a enquete, esse link também chega por
  e-mail (via Resend) — e pode ser reenviado a qualquer momento pela página
  pública da enquete ("Perdeu o link de administração? Clique aqui"),
  informando o mesmo e-mail.
- **Página inicial**: mostra dois botões — "Criar nova enquete" e "Ver
  votações em andamento" (lista todas as enquetes que ainda não encerraram,
  visível para qualquer visitante, sem login — pense nisso se algum dia
  hospedar grupos sem relação entre si na mesma instância).
- **Links curtos**: o link público usa 8 caracteres (`/p/AbC123xy`) e o link
  de admin usa 16 (`/admin/<token>`, ~95 bits de entropia — continua sendo
  um segredo forte, só que mais fácil de copiar e colar do que um UUID).

> **Atenção se você já tinha uma versão anterior rodando**: o esquema do
> banco mudou (a enquete agora tem 3 prazos — indicações, votação 1, votação
> 2 — em vez de 2, e os votos guardam a qual rodada pertencem). Isso não é
> compatível com um `bookvote.db` criado pela versão de uma única votação.
> Como ainda está em fase de testes, o caminho mais simples é apagar o
> volume antigo antes de subir a nova versão:
> ```bash
> docker compose down
> docker volume rm bookvote_bookvote_data   # nome pode variar, veja `docker volume ls`
> docker compose up -d --build
> ```

### Controle anti-bot (camadas, sem exigir conta)

1. Cookie assinado identifica o navegador do votante.
2. Cada IP só pode gerar um número limitado de "identidades" de votante por
   enquete (`BOOKVOTE_MAX_VOTERS_PER_IP`, padrão 6) — dificulta o padrão
   "limpar cookies e votar de novo" em escala.
3. **Log de votos apenas com inserção** (append-only): nenhum voto é
   apagado. Cada envio de cédula grava linhas novas e anula (sem excluir)
   os votos anteriores dessa mesma pessoa (`voter_id`) **ou** desse mesmo
   IP (`ip_hash`) naquela rodada — só a cédula mais recente por IP conta na
   apuração. Isso cria um rastro auditável completo e fecha o brecha de
   "várias identidades de votante, cada uma votando de verdade" dentro do
   limite do item 2 acima.
4. Captcha (Cloudflare Turnstile, gratuito) na indicação e no voto.
5. Rate limiting por IP nas rotas de indicar/votar/criar enquete.

> **Consequência importante do item 3**: se várias pessoas votam a partir
> do mesmo IP (Wi-Fi de casa, escritório, evento), só o voto mais recente
> daquele IP conta — os votos anteriores da mesma rede são anulados, mesmo
> vindos de pessoas diferentes. É uma troca deliberada: prioriza dificultar
> fraude sobre justiça em redes compartilhadas. Se isso for um problema
> para o seu público (ex: evento presencial numa rede única), a proteção
> mais forte de verdade é autenticação real — login por e-mail ou Telegram.

Nenhuma camada isolada é perfeita, mas juntas encarecem bastante o abuso
para uma ferramenta deste porte. Se precisar de algo mais forte no futuro,
o próximo passo natural é login por e-mail (link único) ou Telegram.

**Sobre e-mail (Resend)**: sem `RESEND_API_KEY`, tudo funciona normalmente
— só não envia e-mail de link de administração. No plano gratuito do
Resend, sem domínio verificado, o remetente de testes (`onboarding@resend.dev`)
só entrega para o e-mail cadastrado na sua própria conta Resend — para
enviar a qualquer participante, verifique um domínio em
https://resend.com/domains e troque `RESEND_FROM_EMAIL` no `.env` (ou via
`./scripts/setup_env.sh --resend-from "Nome <voce@seudominio.com>"`).

## Rodar localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # gere BOOKVOTE_SECRET_KEY como indicado no arquivo
uvicorn app.main:app --reload
```

Abra http://localhost:8000 — sem as chaves do Turnstile no `.env`, o captcha
fica desativado automaticamente (bom para testar o fluxo). O `.env` é lido
automaticamente tanto local (via `python-dotenv`) quanto no Docker (via
`env_file`), então não precisa exportar nada manualmente — só editar o
arquivo e reiniciar o `uvicorn`/container.

**Sobre a busca de livros**: sem `GOOGLE_BOOKS_API_KEY`, as buscas usam a
cota pública anônima do Google Books, que é bem pequena e some rápido com
uso normal (aparecem como erro `429 Too Many Requests` no log). Isso não
quebra a ferramenta — a indicação manual continua funcionando — mas para
uso real vale configurar a chave (veja `.env.example`). Depois de editar o
`.env`, confirme no log de inicialização se apareceu "API key carregada":
sem isso, o `uvicorn --reload` às vezes não recarrega variáveis de ambiente
entre reinícios do processo pai — se persistir, pare e rode `uvicorn`
de novo (Ctrl+C e novo `uvicorn app.main:app --reload`).

## Deploy na Oracle Cloud (nginx já instalado na VM)

Pressupondo uma instância Compute (Ubuntu) com nginx já rodando (servindo
outros sites) e portas 80/443 liberadas na Security List/NSG do VCN e no
firewall da própria instância.

1. **Envie os arquivos para a VM** (do seu computador):
   ```bash
   scp -r bookvote/ ubuntu@SEU_IP:~/bookvote
   ```

2. **Na VM, instale Docker e Compose** (se ainda não tiver):
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```

3. **Confirme que as portas 80/443 já estão liberadas** (Security List/NSG
   do VCN de `0.0.0.0/0`, e no firewall local se você usa algo além do
   nginx). Como o nginx já está instalado, provavelmente isso já está feito.

4. **Configure o `.env` com o script de setup** (gera a chave secreta
   automaticamente):
   ```bash
   cd ~/bookvote
   ./scripts/setup_env.sh
   ```
   Ele pergunta as chaves do Turnstile (crie gratuitamente em
   https://dash.cloudflare.com/ → Turnstile — pode deixar em branco para
   testar sem captcha), a chave do Google Books (opcional) e o limite de
   votantes por IP.

   Para deploy automatizado (sem prompts):
   ```bash
   ./scripts/setup_env.sh --yes \
     --turnstile-site SEU_SITE_KEY --turnstile-secret SEU_SECRET_KEY \
     --max-voters 8 --google-books-key SUA_CHAVE
   ```
   Rodar de novo depois não perde a chave secreta já gerada — ele faz
   backup do `.env` anterior e só atualiza o que você passar.

5. **Suba o container** (só a aplicação — sem proxy próprio; o container
   fica acessível apenas em `127.0.0.1:8000`, nunca direto pela internet):
   ```bash
   docker compose up -d --build
   ```

6. **Aponte o nginx para o container**: copie `deploy/nginx-bookvote.conf`
   para `/etc/nginx/sites-available/bookvote`, troque `server_name` pelo
   seu domínio/subdomínio, e ative:
   ```bash
   sudo cp deploy/nginx-bookvote.conf /etc/nginx/sites-available/bookvote
   sudo nano /etc/nginx/sites-available/bookvote   # ajuste o server_name
   sudo ln -s /etc/nginx/sites-available/bookvote /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```

7. **HTTPS**: se você já usa certbot nessa VM para outros sites, é só rodar
   (ele detecta o server block novo e adiciona TLS automaticamente):
   ```bash
   sudo certbot --nginx -d enquete.seudominio.com.br
   ```

8. Acesse `https://enquete.seudominio.com.br`, crie sua primeira enquete e
   guarde o link de admin que aparece após a criação.

> **Por que confiar em `X-Forwarded-For`**: o app usa esse cabeçalho para
> identificar IPs (limite de votantes por rede, rate limiting). O
> `docker-compose.yml` publica o container só em `127.0.0.1` — inacessível
> de fora da própria VM — e o `Dockerfile` roda o uvicorn com
> `--proxy-headers --forwarded-allow-ips='*'`, ou seja, ele confia no
> `X-Forwarded-For` de qualquer coisa que se conecte à porta 8000. Isso só
> é seguro porque, pela topologia de rede, a única coisa que consegue se
> conectar ali é um processo no próprio host (o nginx) — ninguém de fora
> alcança o container direto. O `deploy/nginx-bookvote.conf` já envia esse
> cabeçalho corretamente; se você usar outro proxy no lugar do nginx,
> confirme que ele também envia `X-Forwarded-For` e `X-Forwarded-Proto`.

### Backup

Os dados ficam no volume Docker `bookvote_data` (arquivo SQLite). Para
copiar:
```bash
docker run --rm -v bookvote_data:/data -v $PWD:/backup alpine \
  cp /data/bookvote.db /backup/bookvote-backup.db
```

### Atualizando depois de mudar o código

```bash
docker compose up -d --build
```
