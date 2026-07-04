# Enquete de Livros

Ferramenta pequena para clubes de leitura: indicação de livros → votação
(marca quantos quiser) → resultado com top 3 e sorteio auditável em caso de
empate. Feito em FastAPI + SQLite, sem contas de usuário.

## Como funciona

- **Indicações**: qualquer pessoa com o link público sugere livros até o
  horário definido pelo organizador. Cada pessoa tem um limite configurável
  de indicações.
- **Votação**: a lista trava e vira enquete. Cada visitante pode marcar
  quantos livros quiser e voltar para trocar o voto até o prazo acabar.
- **Resultado**: ao encerrar, os 3 mais votados aparecem. Se houver empate
  na última vaga do top 3, o organizador aciona um sorteio; o resultado do
  sorteio fica registrado (candidatos + seed + vencedor) para qualquer
  pessoa conferir na página pública.
- **Administração**: quem cria a enquete recebe um link secreto de admin
  (`/admin/<token>`) para encerrar fases antes do prazo e acionar o sorteio.

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
