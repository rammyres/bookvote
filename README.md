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
3. Captcha (Cloudflare Turnstile, gratuito) na indicação e no voto.
4. Rate limiting por IP nas rotas de indicar/votar/criar enquete.

Nenhuma camada isolada é perfeita, mas juntas encarecem bastante o abuso
para uma ferramenta deste porte. Se precisar de algo mais forte no futuro,
o próximo passo natural é login por e-mail (link único) ou Telegram.

## Rodar localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # gere BOOKVOTE_SECRET_KEY como indicado no arquivo
uvicorn app.main:app --reload
```

Abra http://localhost:8000 — sem as chaves do Turnstile no `.env`, o captcha
fica desativado automaticamente (bom para testar o fluxo).

## Deploy na Oracle Cloud (VM já criada)

Pressupondo uma instância Compute (Ubuntu) com portas 80/443 liberadas na
Security List/NSG do VCN e no `iptables` da própria instância.

1. **Envie os arquivos para a VM** (do seu computador):
   ```bash
   scp -r bookvote/ ubuntu@SEU_IP:~/bookvote
   ```

2. **Na VM, instale Docker e Compose** (se ainda não tiver):
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-plugin
   sudo usermod -aG docker $USER && newgrp docker
   ```

3. **Libere as portas no firewall da própria instância** (Oracle Linux/Ubuntu
   geralmente bloqueiam por padrão além da Security List do VCN):
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save   # se disponível; senão, ver doc da sua imagem
   ```
   E confirme no painel da Oracle Cloud que a Security List/NSG do VCN libera
   entrada TCP 80 e 443 de `0.0.0.0/0`.

4. **Configure o domínio**: aponte um registro DNS tipo A para o IP público
   da VM. O `Caddyfile` traz `seudominio.com.br` como placeholder — você pode
   editá-lo manualmente ou deixar o script do passo 5 substituir pelo domínio
   real (o Caddy emite certificado HTTPS automaticamente via Let's Encrypt —
   por isso precisa de domínio real e portas 80/443 abertas).

   Se preferir testar só por IP sem domínio/HTTPS por enquanto, troque o
   `Caddyfile` por:
   ```
   :80 {
       reverse_proxy web:8000
   }
   ```
   e depois volte para o domínio quando for para produção (o cookie de
   votante exige `BOOKVOTE_COOKIE_SECURE=true` só funciona bem em HTTPS).

5. **Configure o `.env` com o script de setup** (gera a chave secreta
   automaticamente e já pode atualizar o domínio no `Caddyfile`):
   ```bash
   cd ~/bookvote
   ./scripts/setup_env.sh
   ```
   Ele pergunta as chaves do Turnstile (crie gratuitamente em
   https://dash.cloudflare.com/ → Turnstile — pode deixar em branco para
   testar sem captcha), o limite de votantes por IP e o domínio.

   Para deploy automatizado (sem prompts), passe tudo via flags, por exemplo:
   ```bash
   ./scripts/setup_env.sh --yes \
     --turnstile-site SEU_SITE_KEY --turnstile-secret SEU_SECRET_KEY \
     --max-voters 8 --domain enquete.seudominio.com.br
   ```
   Rodar o script de novo depois não perde a chave secreta já gerada nem
   as outras configs — ele faz backup do `.env`/`Caddyfile` anteriores e só
   atualiza o que você passar.

6. **Suba os containers**:
   ```bash
   docker compose up -d --build
   ```

7. Acesse `https://seudominio.com.br`, crie sua primeira enquete e guarde o
   link de admin que aparece após a criação.

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
