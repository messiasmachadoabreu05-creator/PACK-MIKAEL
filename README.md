# Bot Discord Mikael KEY

Bot em Python para tickets, estoque de KEYS ligado ao Supabase do site, calls temporárias e moderação com revisão manual.

## Antes de iniciar

1. Instale Python 3.10 ou superior.
2. No Discord Developer Portal, crie/abra a aplicação do bot, copie o token e habilite **Message Content Intent** e **Server Members Intent**. Convide com os escopos `bot` e `applications.commands`; dê permissões para gerenciar canais, ler/enviar mensagens, incorporar links, gerenciar mensagens, moderar membros, mover/conectar membros e banir (caso escolha banimento).
3. No painel Supabase, faça backup do projeto e execute `supabase-key-bot.sql` no SQL Editor. A migração espera a tabela `public.keys` existente com `id`, `code`, `plan`, `redeemed_by` e `created_at`, e a tabela `public.profiles` do site com `key_id` e `device_id` para limpar vínculos numa exclusão.
4. Copie `.env.example` para `.env` e preencha `DISCORD_TOKEN`, `OWNER_ID` e `SUPABASE_SERVICE_ROLE_KEY`. O endereço do projeto já está preenchido com o projeto identificado no `script.js` do site. A service role key é secreta: mantenha-a somente neste arquivo local/servidor, nunca no site, GitHub ou mensagem pública.
5. Instale e execute:

```powershell
py -m pip install -r requirements.txt
py main.py
```

O bot cria `bot.sqlite3` na pasta e sincroniza os comandos slash ao iniciar. Mantenha o processo ativo no host para atender interações e remover calls vencidas.

## Comandos

- `/ajuda` — lista de comandos. `?ajuda` e menção ao bot também respondem.
- `/config-dono` — cargos de dono/staff e categorias para tickets, tickets de KEY e calls.
- `/config-ticket` — abre formulário e publica no canal atual um painel de Suporte, Dúvidas, Resgatar KEY e Divulgação. Staff pode assumir; dono ou staff fecha em cinco segundos.
- `/config-keys`, `/enviar-painel-keys` — configura e publica painel com botão para resgatar e link do site.
- `/config-regras-key` — texto obrigatório exibido nos tickets privados de KEY.
- `/config-key-gerar` — intervalo, em dias, entre entregas por usuário; zero desativa o limite.
- `/gerar-key` — cria de 1 a 1000 KEYS no Supabase com validade em dias (zero = vitalícia).
- `/inspecionar-key` — mostra estado sem revelar o código completo; permite desativar ou excluir com confirmação.
- `/dar-key` — pede confirmação, reserva e entrega uma KEY a uma pessoa.
- No ticket de KEY, **Enviar KEY** inicia a validade no momento da entrega; **Fechar KEY** somente o dono pode usar. Se fechar sem entregar, a reserva volta ao estoque.
- `/config-call`, `/config-call-intervalo`, `/enviar-painel-call` — configura e publica criação de call pública/privada; categoria em `/config-dono`, uma call ativa por usuário e limite por servidor. Calls são removidas após cinco horas ou cinco minutos vazias.
- `/config-moderacao`, `/config-moderacao-canais` — palavras sinalizadas, flood e canais de revisão. Botões de ignorar/aplicar punição são restritos ao dono; punição: 10 minutos, 1 dia e no terceiro aviso 7 dias ou ban.

## KEYS e o site

O código usa o estoque existente em `public.keys`, cria novas linhas com `code`, `plan` e `duration_days`, e reserva atomicamente uma KEY por ticket via funções SQL. A migração armazena o prazo em `expires_at` a partir do envio pelo bot; quando o site atualizar os campos de ativação, o gatilho preserva essa validade. O tempo entre entregas é outra configuração, independente da validade da KEY.

Não foi fornecida a definição atual das funções SQL do site (`check_key` e `redeem_key`), por isso não substituí essas funções. A migração foi escrita a partir das colunas observadas no `script.js` e deve ser conferida contra o schema atual antes de executar. O bot precisa da chave service role válida e do schema aplicado para que os comandos de KEY funcionem.

## Emojis e aparência

Os emojis personalizados fornecidos foram adicionados com os IDs da imagem; os nomes com `_animado` estão marcados como animados. O bot precisa estar no servidor onde esses emojis existem. A cor das embeds/painéis usa azul.

## Limites de verificação

O arquivo Python foi validado por compilação sintática local. A conexão real com Discord/Supabase depende do token, permissões e chave privada do projeto; não é possível afirmar que integrações externas estão operacionais até iniciar o bot com essas credenciais e aplicar a migração.
