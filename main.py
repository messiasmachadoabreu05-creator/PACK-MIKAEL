from __future__ import annotations
import asyncio, json, os, re, secrets, sqlite3, string, time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()
ROOT=Path(__file__).resolve().parent
DB_PATH=ROOT/'bot.sqlite3'
TOKEN=os.getenv('DISCORD_TOKEN','').strip()
OWNER_ID=int(os.getenv('OWNER_ID','0') or 0)
SUPABASE_URL=os.getenv('SUPABASE_URL','').strip()
SUPABASE_SERVICE_ROLE_KEY=os.getenv('SUPABASE_SERVICE_ROLE_KEY','').strip()
SUPA: Client|None=create_client(SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY) if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY else None

EMOJIS={
 'anuncio_animado':('1553201646022959164',True),'perfil_usuario':('1552006830224441556',False),
 'ticket':('1552006576703799327',False),'suporte':('1552006560430039080',False),
 'coins':('1552006441068396674',False),'ajuda':('1552006414438760600',False),
 'verificado':('1552006332729524335',False),'key':('1552006330523455560',False),
 'caradinheiro_animado':('1552006329109971106',True),'staff':('1550581194310025286',False),
 'erro_animado':('1550581064177688657',True),'sucesso_animado':('1550581019072270447',True),
 'cadeado_privado':('1550580961907970088',False)}
BRAND_BLUE=discord.Color.from_rgb(35,110,255)
def em(name):
    eid,animated=EMOJIS[name]
    return discord.PartialEmoji(name=name,id=int(eid),animated=animated)
def em_text(name):
    eid,animated=EMOJIS[name]
    return f"<{ 'a' if animated else ''}:{name}:{eid}>"
@contextmanager
def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row
    try:
        with c: yield c
    finally: c.close()
def init_db():
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS settings(guild INTEGER,name TEXT,value TEXT,PRIMARY KEY(guild,name))')
        c.execute('CREATE TABLE IF NOT EXISTS tickets(channel INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,kind TEXT,claimed INTEGER,key_id TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS calls(guild INTEGER,owner INTEGER,voice INTEGER PRIMARY KEY,control INTEGER,created REAL,empty_since REAL)')
        c.execute('CREATE TABLE IF NOT EXISTS key_cooldowns(guild INTEGER,user INTEGER,until REAL,PRIMARY KEY(guild,user))')
        c.execute('CREATE TABLE IF NOT EXISTS call_cooldowns(guild INTEGER,user INTEGER,until REAL,PRIMARY KEY(guild,user))')
        c.execute('CREATE TABLE IF NOT EXISTS mod_reports(message INTEGER PRIMARY KEY,guild INTEGER,user INTEGER,channel INTEGER,content TEXT,reason TEXT,level INTEGER,created REAL,state TEXT NOT NULL DEFAULT "pending")')
        c.execute('CREATE TABLE IF NOT EXISTS warn_counts(guild INTEGER,user INTEGER,count INTEGER,PRIMARY KEY(guild,user))')
def get(guild,name,default=None):
    with db() as c: r=c.execute('SELECT value FROM settings WHERE guild=? AND name=?',(guild,name)).fetchone()
    return json.loads(r['value']) if r else default
def put(guild,name,value):
    with db() as c: c.execute('INSERT INTO settings VALUES(?,?,?) ON CONFLICT(guild,name) DO UPDATE SET value=excluded.value',(guild,name,json.dumps(value)))
def owner(i):
    if not i.guild: return False
    if i.user.id==i.guild.owner_id or (OWNER_ID and i.user.id==OWNER_ID): return True
    rid=get(i.guild.id,'owner_role')
    return bool(rid and isinstance(i.user,discord.Member) and any(r.id==rid for r in i.user.roles))
def staff(i):
    if owner(i): return True
    rid=get(i.guild.id,'staff_role') if i.guild else None
    return bool(rid and isinstance(i.user,discord.Member) and any(r.id==rid for r in i.user.roles))
def require_supa():
    if not SUPA: raise RuntimeError('Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no .env.')
def rpc(name,args):
    require_supa(); return SUPA.rpc(name,args).execute().data

def key_code():
    chars='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return '-'.join(''.join(secrets.choice(chars) for _ in range(4)) for _ in range(4))
def mask(code):
    p=code.split('-'); return f'{p[0]}-****-{p[-1]}' if len(p)>1 else '****'
def parse_rows(data):
    if isinstance(data,list): return data
    return [data] if data else []
def record_ticket(channel,guild,user,kind,key_id=None):
    with db() as c: c.execute('INSERT OR REPLACE INTO tickets(channel,guild,user,kind,key_id) VALUES(?,?,?,?,?)',(channel,guild,user,kind,key_id))

intents=discord.Intents.default(); intents.guilds=True; intents.members=True; intents.message_content=True; intents.voice_states=True
class Bot(commands.Bot):
    async def setup_hook(self):
        init_db()
        for v in [TicketPanel(),TicketActions(),KeyPanel(),KeyTicketActions(),CallPanel(),CallActions(),ModActions()]: self.add_view(v)
        await self.tree.sync()
        self.loop.create_task(call_cleanup_loop())
bot=Bot(command_prefix='?',intents=intents,help_command=None)

async def create_ticket(guild,user,kind,key=False):
    cat_key='key_category' if key else 'ticket_category'; cid=get(guild.id,cat_key)
    category=guild.get_channel(cid) if cid else None
    if not isinstance(category,discord.CategoryChannel): raise RuntimeError('Configure a categoria em /config-dono.')
    overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
    if guild.me: overwrites[guild.me]=discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True)
    if key:
        with db() as c:
            active=c.execute("SELECT channel FROM tickets WHERE guild=? AND user=? AND kind='key'",(guild.id,user.id)).fetchone()
            cd=c.execute('SELECT until FROM key_cooldowns WHERE guild=? AND user=?',(guild.id,user.id)).fetchone()
        if active: raise RuntimeError('Você já tem um ticket de KEY aberto.')
        if cd and cd['until']>time.time(): raise RuntimeError(f'Você poderá resgatar outra KEY em {datetime.fromtimestamp(cd["until"],timezone.utc).astimezone().strftime("%d/%m %H:%M")} (horário local).')
        rid=get(guild.id,'owner_role'); role=guild.get_role(rid) if rid else None
        if role: overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        else: overwrites[guild.owner]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
    else:
        rid=get(guild.id,'staff_role'); role=guild.get_role(rid) if rid else None
        if role: overwrites[role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
    slug=re.sub('[^a-z0-9-]','',user.name.lower().replace(' ','-'))[:35] or str(user.id)
    channel=await guild.create_text_channel(f"{'key' if key else 'ticket'}-{slug}",category=category,overwrites=overwrites)
    key_id=None
    if key:
        try:
            rows=parse_rows(rpc('bot_reserve_key',{'p_discord_user_id':user.id,'p_ticket_channel_id':channel.id}))
            if not rows: await channel.delete(reason='Sem keys disponíveis'); raise RuntimeError('No momento não há keys disponíveis.')
            key_id=str(rows[0]['id'])
        except Exception:
            await channel.delete(reason='Falha ao reservar KEY'); raise
    record_ticket(channel.id,guild.id,user.id,'key' if key else kind,key_id)
    if key:
        rules=get(guild.id,'key_rules','Envie as provas solicitadas e aguarde a análise do dono.')
        embed=discord.Embed(title='Resgate de KEY',description=rules,color=BRAND_BLUE)
        embed.add_field(name='Usuário',value=user.mention)
        await channel.send(embed=embed,view=KeyTicketActions())
    else:
        embed=discord.Embed(title=f'TICKET: {kind.upper()}',description=f'{user.mention}\nExplique sua solicitação com detalhes. Apenas você e a equipe autorizada podem ver este canal.',color=BRAND_BLUE)
        await channel.send(embed=embed,view=TicketActions())
    return channel

class TicketPicker(discord.ui.Select):
    def __init__(self):
        opts=[discord.SelectOption(label='Suporte',value='suporte',emoji=em('suporte')),discord.SelectOption(label='Dúvidas',value='duvidas',emoji=em('ajuda')),discord.SelectOption(label='Resgatar KEY',value='key',emoji=em('key')),discord.SelectOption(label='Divulgação',value='divulgacao',emoji=em('anuncio_animado'))]
        super().__init__(placeholder='Selecione uma opção de atendimento',options=opts,custom_id='ticket:pick')
    async def callback(self,i):
        if not i.guild: return await i.response.send_message('Use no servidor.',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try: ch=await create_ticket(i.guild,i.user,self.values[0],self.values[0]=='key')
        except Exception as e: return await i.followup.send(f'Não consegui abrir: {e}',ephemeral=True)
        await i.followup.send(f'Ticket criado: {ch.mention}',ephemeral=True)
class TicketPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None); self.add_item(TicketPicker())

async def close_ticket(i,key_only=False):
    if not isinstance(i.channel,discord.TextChannel): return await i.response.send_message('Use dentro do ticket.',ephemeral=True)
    with db() as c: row=c.execute('SELECT * FROM tickets WHERE channel=?',(i.channel.id,)).fetchone()
    if not row: return await i.response.send_message('Registro de ticket não encontrado.',ephemeral=True)
    if key_only:
        allowed=owner(i)
    else: allowed=owner(i) or staff(i)
    if not allowed: return await i.response.send_message('Você não pode fechar este ticket.',ephemeral=True)
    if row['key_id'] and SUPA:
        try: rpc('bot_release_key',{'p_ticket_channel_id':i.channel.id})
        except Exception: pass
    await i.response.send_message('Ticket será fechado em 5 segundos.')
    await asyncio.sleep(5)
    with db() as c: c.execute('DELETE FROM tickets WHERE channel=?',(i.channel.id,))
    await i.channel.delete(reason=f'Fechado por {i.user}')
class TicketActions(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Assumir ticket',style=discord.ButtonStyle.primary,custom_id='ticket:claim',emoji=em('staff'))
    async def claim(self,i,b):
        if not staff(i): return await i.response.send_message('Somente dono ou staff pode assumir.',ephemeral=True)
        if not isinstance(i.channel,discord.TextChannel): return await i.response.send_message('Ação inválida.',ephemeral=True)
        with db() as c: row=c.execute('SELECT * FROM tickets WHERE channel=?',(i.channel.id,)).fetchone()
        if not row or row['kind']=='key': return await i.response.send_message('Ticket inválido.',ephemeral=True)
        if row['claimed']: return await i.response.send_message('Este ticket já foi assumido.',ephemeral=True)
        rid=get(i.guild.id,'staff_role'); role=i.guild.get_role(rid) if rid else None
        if role: await i.channel.set_permissions(role,view_channel=False,send_messages=False)
        await i.channel.set_permissions(i.user,view_channel=True,send_messages=True,read_message_history=True)
        with db() as c: c.execute('UPDATE tickets SET claimed=? WHERE channel=?',(i.user.id,i.channel.id))
        await i.response.send_message(f'{i.user.mention} assumiu este ticket.')
    @discord.ui.button(label='Fechar ticket',style=discord.ButtonStyle.danger,custom_id='ticket:close',emoji=em('cadeado_privado'))
    async def close(self,i,b): await close_ticket(i)
class KeyTicketActions(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Fechar KEY',style=discord.ButtonStyle.secondary,custom_id='keyticket:close',emoji=em('cadeado_privado'))
    async def close(self,i,b): await close_ticket(i,True)
    @discord.ui.button(label='Enviar KEY',style=discord.ButtonStyle.success,custom_id='keyticket:deliver',emoji=em('key'))
    async def deliver(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode entregar KEY.',ephemeral=True)
        if not isinstance(i.channel,discord.TextChannel): return await i.response.send_message('Ação inválida.',ephemeral=True)
        try: rows=parse_rows(rpc('bot_deliver_key',{'p_ticket_channel_id':i.channel.id}))
        except Exception as e: return await i.response.send_message(f'Falha na entrega: {e}',ephemeral=True)
        if not rows: return await i.response.send_message('Não há KEY reservada neste ticket.',ephemeral=True)
        r=rows[0]
        with db() as c: t=c.execute('SELECT user FROM tickets WHERE channel=?',(i.channel.id,)).fetchone()
        member=i.guild.get_member(t['user']) if t else None
        validity='vitalícia' if not r.get('expires_at') else f'{r.get("duration_days")} dias'
        message=f'Sua KEY é `{r["code"]}`. A validade é {validity} e começou no momento deste envio.'
        interval=get(i.guild.id,'key_interval_days',0)
        if interval:
            with db() as c: c.execute('INSERT INTO key_cooldowns(guild,user,until) VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET until=excluded.until',(i.guild.id,t['user'],time.time()+interval*86400))
        sent=False
        if member:
            try: await member.send(message); sent=True
            except discord.Forbidden: pass
        await i.response.send_message(message if not sent else f'{em_text("sucesso_animado")} KEY enviada por DM; validade iniciada agora.')

class TicketModal(discord.ui.Modal,title='Criar painel de ticket'):
    titulo=discord.ui.TextInput(label='Título',max_length=100)
    mensagem=discord.ui.TextInput(label='Mensagem',style=discord.TextStyle.paragraph,max_length=1500)
    imagem=discord.ui.TextInput(label='Link da imagem PNG (opcional)',required=False,max_length=500)
    async def on_submit(self,i):
        if not i.guild or not owner(i): return await i.response.send_message('Só o dono configura painéis.',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        embed=discord.Embed(title=str(self.titulo),description=str(self.mensagem),color=BRAND_BLUE)
        if self.imagem.value: embed.set_image(url=str(self.imagem.value))
        await i.channel.send(embed=embed,view=TicketPanel()); await i.followup.send('Painel enviado neste canal.',ephemeral=True)

# Configuração geral de cargos e categorias
class RolePicker(discord.ui.RoleSelect):
    def __init__(self,key,label): self.key=key; super().__init__(placeholder=label,min_values=1,max_values=1)
    async def callback(self,i): put(i.guild.id,self.key,self.values[0].id); await i.response.send_message(f'Salvo: {self.values[0].mention}',ephemeral=True)
class CategoryPicker(discord.ui.ChannelSelect):
    def __init__(self,key,label): self.key=key; super().__init__(placeholder=label,channel_types=[discord.ChannelType.category],min_values=1,max_values=1)
    async def callback(self,i): put(i.guild.id,self.key,self.values[0].id); await i.response.send_message(f'Categoria salva: {self.values[0].name}',ephemeral=True)
class ConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        for k,l in [('owner_role','Cargo dono'),('staff_role','Cargo staff')]: self.add_item(RolePicker(k,l))
        for k,l in [('ticket_category','Categoria ticket'),('key_category','Categoria ticket KEY'),('call_category','Categoria das calls')]: self.add_item(CategoryPicker(k,l))

# Painel e administração de KEY
class KeyPanelModal(discord.ui.Modal,title='Configurar painel de KEYS'):
    titulo=discord.ui.TextInput(label='Título',max_length=100)
    descricao=discord.ui.TextInput(label='Descrição',style=discord.TextStyle.paragraph,max_length=1500)
    imagem=discord.ui.TextInput(label='Link da imagem PNG (opcional)',required=False,max_length=500)
    site=discord.ui.TextInput(label='Link do site',max_length=500)
    async def on_submit(self,i):
        if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode configurar.',ephemeral=True)
        put(i.guild.id,'keys_panel',{'title':str(self.titulo),'description':str(self.descricao),'image':str(self.imagem.value or ''),'site':str(self.site)})
        await i.response.send_message('Painel de KEYS salvo. Use /enviar-painel-keys no canal desejado.',ephemeral=True)
class RedeemKeyButton(discord.ui.Button):
    def __init__(self): super().__init__(label='Resgatar KEY',style=discord.ButtonStyle.success,custom_id='keypanel:redeem',emoji=em('key'))
    async def callback(self,i):
        if not i.guild: return await i.response.send_message('Use no servidor.',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try: channel=await create_ticket(i.guild,i.user,'key',True)
        except Exception as e: return await i.followup.send(f'Não consegui abrir o pedido: {e}',ephemeral=True)
        await i.followup.send(f'Pedido de KEY criado: {channel.mention}',ephemeral=True)
class KeyPanel(discord.ui.View):
    def __init__(self,site=''):
        super().__init__(timeout=None); self.add_item(RedeemKeyButton())
        if site.startswith(('https://','http://')): self.add_item(discord.ui.Button(label='Nosso site',style=discord.ButtonStyle.link,url=site,emoji=em('verificado')))

class RulesModal(discord.ui.Modal,title='Regras para resgatar KEY'):
    rules=discord.ui.TextInput(label='Regras exibidas no ticket',style=discord.TextStyle.paragraph,max_length=1800)
    async def on_submit(self,i):
        if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode alterar as regras.',ephemeral=True)
        put(i.guild.id,'key_rules',str(self.rules)); await i.response.send_message('Regras de KEY salvas.',ephemeral=True)

class InspectKeyActions(discord.ui.View):
    def __init__(self,key_id): super().__init__(timeout=180); self.key_id=key_id
    @discord.ui.button(label='Desativar KEY',style=discord.ButtonStyle.danger,custom_id='key:revoke')
    async def revoke(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode desativar.',ephemeral=True)
        try: result=rpc('bot_revoke_key',{'p_key_id':self.key_id})
        except Exception as e: return await i.response.send_message(f'Erro ao desativar: {e}',ephemeral=True)
        await i.response.send_message('KEY desativada.' if result else 'KEY não encontrada.',ephemeral=True)
    @discord.ui.button(label='Excluir KEY',style=discord.ButtonStyle.secondary,custom_id='key:delete')
    async def delete(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode excluir.',ephemeral=True)
        await i.response.send_message('Confirma excluir esta KEY?',view=ConfirmDeleteKey(self.key_id),ephemeral=True)
class ConfirmDeleteKey(discord.ui.View):
    def __init__(self,key_id): super().__init__(timeout=60); self.key_id=key_id
    @discord.ui.button(label='Sim, excluir',style=discord.ButtonStyle.danger,custom_id='key:delete:yes')
    async def yes(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode excluir.',ephemeral=True)
        try:
            SUPA.table('profiles').update({'key_id':None,'device_id':None}).eq('key_id',self.key_id).execute()
            SUPA.table('keys').delete().eq('id',self.key_id).execute()
        except Exception as e: return await i.response.send_message(f'Erro ao excluir: {e}',ephemeral=True)
        await i.response.edit_message(content='KEY excluída.',view=None)
    @discord.ui.button(label='Cancelar',style=discord.ButtonStyle.secondary,custom_id='key:delete:no')
    async def no(self,i,b): await i.response.edit_message(content='Exclusão cancelada.',view=None)

class ConfirmGiveKey(discord.ui.View):
    def __init__(self,member): super().__init__(timeout=60); self.member=member
    @discord.ui.button(label='Sim, entregar',style=discord.ButtonStyle.success,custom_id='key:give:yes')
    async def yes(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode dar KEY.',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            ticket=await create_ticket(i.guild,self.member,'key',True)
            rows=parse_rows(rpc('bot_deliver_key',{'p_ticket_channel_id':ticket.id}))
            if not rows: raise RuntimeError('Nenhuma KEY disponível para entrega.')
            k=rows[0]; text=f'Sua KEY é `{k["code"]}`. A validade começa agora e dura {k.get("duration_days") or "vitalícia"} dias.'
            interval=get(i.guild.id,'key_interval_days',0)
            if interval:
                with db() as c: c.execute('INSERT INTO key_cooldowns(guild,user,until) VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET until=excluded.until',(i.guild.id,self.member.id,time.time()+interval*86400))
            try: await self.member.send(text); destination='Enviada por DM.'
            except discord.Forbidden: await ticket.send(text); destination=f'DM fechada; entregue no ticket privado {ticket.mention}.'
            await i.followup.send(destination,ephemeral=True)
        except Exception as e: await i.followup.send(f'Não consegui entregar: {e}',ephemeral=True)
    @discord.ui.button(label='Não',style=discord.ButtonStyle.secondary,custom_id='key:give:no')
    async def no(self,i,b): await i.response.edit_message(content='Entrega cancelada.',view=None)

@bot.tree.command(name='config-keys',description='Configura título, descrição, imagem e link do painel de KEYS')
async def config_keys(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode configurar.',ephemeral=True)
    await i.response.send_modal(KeyPanelModal())
@bot.tree.command(name='enviar-painel-keys',description='Publica o painel de resgate de KEYS neste canal')
async def enviar_painel_keys(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode publicar.',ephemeral=True)
    cfg=get(i.guild.id,'keys_panel')
    if not cfg: return await i.response.send_message('Configure primeiro com /config-keys.',ephemeral=True)
    embed=discord.Embed(title=cfg['title'],description=cfg['description'],color=BRAND_BLUE)
    if cfg.get('image'): embed.set_image(url=cfg['image'])
    await i.channel.send(embed=embed,view=KeyPanel(cfg.get('site',''))); await i.response.send_message('Painel de KEYS enviado.',ephemeral=True)
@bot.tree.command(name='config-regras-key',description='Edita as regras exibidas nos tickets de KEY')
async def config_regras_key(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode configurar.',ephemeral=True)
    await i.response.send_modal(RulesModal())
@bot.tree.command(name='config-key-gerar',description='Define o intervalo mínimo entre resgates por usuário')
@app_commands.describe(dias='Dias até o próximo resgate; 0 deixa sem limite')
async def config_key_gerar(i,dias:app_commands.Range[int,0,3650]):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode configurar.',ephemeral=True)
    put(i.guild.id,'key_interval_days',dias); await i.response.send_message(f'Intervalo entre resgates: {dias} dias.',ephemeral=True)
@bot.tree.command(name='gerar-key',description='Gera KEYS disponíveis no site')
@app_commands.describe(quantidade='De 1 a 1000',dias='Validade em dias; 0 significa vitalícia')
async def gerar_key(i,quantidade:app_commands.Range[int,1,1000],dias:app_commands.Range[int,0,3650]):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode gerar KEYS.',ephemeral=True)
    await i.response.defer(ephemeral=True,thinking=True)
    try:
        require_supa(); plan={7:'7d',30:'30d',365:'365d',0:'vitalicia'}.get(dias,'custom')
        rows=[{'code':key_code(),'plan':plan,'duration_days':dias or None,'bot_status':'available'} for _ in range(quantidade)]
        SUPA.table('keys').insert(rows).execute()
    except Exception as e: return await i.followup.send(f'Não consegui gerar no Supabase. Confira a migração SQL: {e}',ephemeral=True)
    await i.followup.send(f'{quantidade} KEY(s) de {dias or "validade vitalícia"} dias adicionadas ao Supabase.',ephemeral=True)
@bot.tree.command(name='inspecionar-key',description='Inspeciona uma KEY sem mostrar o código completo')
@app_commands.describe(codigo='Código da KEY')
async def inspecionar_key(i,codigo:str):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode inspecionar KEYS.',ephemeral=True)
    try: rows=parse_rows(rpc('bot_inspect_key',{'p_code':codigo.strip().upper()}))
    except Exception as e: return await i.response.send_message(f'Erro de conexão: {e}',ephemeral=True)
    if not rows: return await i.response.send_message('KEY não encontrada.',ephemeral=True)
    k=rows[0]; embed=discord.Embed(title='Inspeção de KEY',color=BRAND_BLUE)
    embed.add_field(name='Código',value=mask(k['code']),inline=False); embed.add_field(name='Plano',value=k.get('plan') or 'custom')
    embed.add_field(name='Estado',value=k.get('bot_status') or ('resgatada' if k.get('redeemed_by') else 'disponível'))
    embed.add_field(name='Expira',value=k.get('expires_at') or 'Ainda sem expiração')
    await i.response.send_message(embed=embed,view=InspectKeyActions(str(k['id'])),ephemeral=True)
@bot.tree.command(name='dar-key',description='Confirma a entrega direta de uma KEY para uma pessoa')
async def dar_key(i,pessoa:discord.Member):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono pode usar /dar-key.',ephemeral=True)
    await i.response.send_message(f'Quer entregar uma KEY para {pessoa.mention}?',view=ConfirmGiveKey(pessoa),ephemeral=True)

@bot.tree.command(name='config-dono',description='Configura cargos e categorias do bot')
async def config_dono(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
    await i.response.send_message('Selecione cargos e categorias. A categoria de voz das calls também fica aqui.',view=ConfigView(),ephemeral=True)

@bot.tree.command(name='config-ticket',description='Configura e publica o painel de tickets neste canal')
async def config_ticket(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
    await i.response.send_modal(TicketModal())

class CallConfigModal(discord.ui.Modal,title='Configurar calls'):
    titulo=discord.ui.TextInput(label='Título do painel',default='Criar sua call',max_length=100)
    descricao=discord.ui.TextInput(label='Descrição',default='Selecione o tamanho e escolha se sua call será pública ou privada.',style=discord.TextStyle.paragraph,max_length=800)
    tamanhos=discord.ui.TextInput(label='Tamanhos separados por vírgula',default='2,4,6,10',max_length=80)
    limite=discord.ui.TextInput(label='Máximo de calls simultâneas no servidor',default='5',max_length=3)
    imagem=discord.ui.TextInput(label='Link da imagem PNG (opcional)',required=False,max_length=500)
    async def on_submit(self,i):
        if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
        try:
            caps=sorted({int(x.strip()) for x in str(self.tamanhos).split(',') if x.strip()})
            limit=int(str(self.limite))
            if not caps or min(caps)<2 or max(caps)>99 or not 1<=limit<=50: raise ValueError()
        except ValueError: return await i.response.send_message('Revise os tamanhos (2–99), limite (1–50) e intervalo.',ephemeral=True)
        old=get(i.guild.id,'call_config',{})
        put(i.guild.id,'call_config',{'title':str(self.titulo),'description':str(self.descricao),'sizes':caps,'max':limit,'cooldown':old.get('cooldown',1),'image':str(self.imagem.value or '')})
        await i.response.send_message('Configuração de calls salva. Use /enviar-painel-call para publicar.',ephemeral=True)
class CallPrivacyView(discord.ui.View):
    def __init__(self,capacity): super().__init__(timeout=120); self.capacity=capacity
    async def create(self,i,private):
        if not i.guild: return await i.response.send_message('Use no servidor.',ephemeral=True)
        cfg=get(i.guild.id,'call_config',{'max':5,'cooldown':1})
        category_id=get(i.guild.id,'call_category'); category=i.guild.get_channel(category_id) if category_id else None
        if not isinstance(category,discord.CategoryChannel): return await i.response.send_message('Configure a categoria de calls em /config-dono.',ephemeral=True)
        now=time.time()
        with db() as c:
            active=c.execute('SELECT * FROM calls WHERE guild=? AND owner=?',(i.guild.id,i.user.id)).fetchone()
            count=c.execute('SELECT COUNT(*) n FROM calls WHERE guild=?',(i.guild.id,)).fetchone()['n']
            cd=c.execute('SELECT until FROM call_cooldowns WHERE guild=? AND user=?',(i.guild.id,i.user.id)).fetchone()
        if active: return await i.response.send_message('Você já tem uma call ativa.',ephemeral=True)
        if count>=cfg.get('max',5): return await i.response.send_message('O servidor atingiu o limite de calls ativas.',ephemeral=True)
        if cd and cd['until']>now: return await i.response.send_message(f'Aguarde {int((cd["until"]-now)/60)+1} minuto(s) para criar outra call.',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        overwrites={i.guild.default_role:discord.PermissionOverwrite(view_channel=not private,connect=not private),i.user:discord.PermissionOverwrite(view_channel=True,connect=True,manage_channels=True)}
        if i.guild.me: overwrites[i.guild.me]=discord.PermissionOverwrite(view_channel=True,connect=True,manage_channels=True)
        voice=await i.guild.create_voice_channel(f'call-{i.user.name[:60]}',category=category,user_limit=self.capacity,overwrites=overwrites)
        control=await i.guild.create_text_channel(f'controle-{i.user.name[:50]}',category=category,overwrites=overwrites)
        with db() as c:
            c.execute('INSERT INTO calls(guild,owner,voice,control,created,empty_since) VALUES(?,?,?,?,?,NULL)',(i.guild.id,i.user.id,voice.id,control.id,now))
            c.execute('INSERT INTO call_cooldowns(guild,user,until) VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET until=excluded.until',(i.guild.id,i.user.id,now+cfg.get('cooldown',1)*3600))
        await control.send(f'Call de {i.user.mention}: {voice.mention} · limite {self.capacity} · {"privada" if private else "pública"}.',view=CallActions())
        await i.response.send_message(f'Call criada: {voice.mention}. Ela será removida após 5 horas ou 5 minutos vazia.',ephemeral=True)
    @discord.ui.button(label='Pública',style=discord.ButtonStyle.primary,custom_id='call:public',emoji=em('verificado'))
    async def public(self,i,b): await self.create(i,False)
    @discord.ui.button(label='Privada',style=discord.ButtonStyle.secondary,custom_id='call:private',emoji=em('cadeado_privado'))
    async def private(self,i,b): await self.create(i,True)
class CallSizeSelect(discord.ui.Select):
    def __init__(self,guild):
        cfg=get(guild.id,'call_config',{'sizes':[2,4,6,10]}) if guild else {'sizes':[2,4,6,10]}
        opts=[discord.SelectOption(label=f'{n} pessoas',value=str(n),emoji=em('perfil_usuario')) for n in cfg.get('sizes',[2,4,6,10])[:25]]
        super().__init__(placeholder='Quantas pessoas?',options=opts,custom_id='call:size')
    async def callback(self,i): await i.response.send_message(f'Call para {self.values[0]} pessoas. Escolha o tipo:',view=CallPrivacyView(int(self.values[0])),ephemeral=True)
class CallPanel(discord.ui.View):
    def __init__(self,guild=None):
        super().__init__(timeout=None)
        self.add_item(CallSizeSelect(guild))
class CallActions(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CallInviteSelect())
    async def call_row(self,i):
        with db() as c: return c.execute('SELECT * FROM calls WHERE control=?',(i.channel.id,)).fetchone()
    @discord.ui.button(label='Encerrar call',style=discord.ButtonStyle.danger,custom_id='call:end')
    async def end(self,i,b):
        row=await self.call_row(i)
        if not row or (i.user.id!=row['owner'] and not owner(i)): return await i.response.send_message('Só quem criou ou o dono pode encerrar.',ephemeral=True)
        await i.response.send_message('Encerrando call.')
        await delete_call(i.guild,row)
    @discord.ui.button(label='Travar / destravar',style=discord.ButtonStyle.secondary,custom_id='call:lock')
    async def lock(self,i,b):
        row=await self.call_row(i)
        if not row or (i.user.id!=row['owner'] and not owner(i)): return await i.response.send_message('Só quem criou ou o dono pode alterar.',ephemeral=True)
        voice=i.guild.get_channel(row['voice'])
        if not isinstance(voice,discord.VoiceChannel): return await i.response.send_message('Call não encontrada.',ephemeral=True)
        current=voice.overwrites_for(i.guild.default_role); current.connect=None if current.connect is False else False
        await voice.set_permissions(i.guild.default_role,overwrite=current)
        await i.response.send_message('Acesso atualizado.',ephemeral=True)
class CallInviteSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Convidar pessoas para a call',min_values=1,max_values=10,custom_id='call:invite')
    async def callback(self,i):
        with db() as c: row=c.execute('SELECT * FROM calls WHERE control=?',(i.channel.id,)).fetchone()
        if not row or (i.user.id!=row['owner'] and not owner(i)):
            return await i.response.send_message('Só quem criou ou o dono pode convidar.',ephemeral=True)
        voice=i.guild.get_channel(row['voice'])
        if not isinstance(voice,discord.VoiceChannel):
            return await i.response.send_message('Call não encontrada.',ephemeral=True)
        for member in self.values:
            await voice.set_permissions(member,view_channel=True,connect=True)
        await i.response.send_message('Convites aplicados.',ephemeral=True)
async def delete_call(guild,row):
    voice=guild.get_channel(row['voice']); control=guild.get_channel(row['control'])
    if voice: await voice.delete(reason='Fim de call')
    if control: await control.delete(reason='Fim de call')
    with db() as c: c.execute('DELETE FROM calls WHERE voice=?',(row['voice'],))
async def call_cleanup_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            with db() as c: rows=c.execute('SELECT * FROM calls').fetchall()
            now=time.time()
            for row in rows:
                guild=bot.get_guild(row['guild']); voice=guild.get_channel(row['voice']) if guild else None
                if not guild or not voice: 
                    with db() as c: c.execute('DELETE FROM calls WHERE voice=?',(row['voice'],))
                    continue
                if now-row['created']>=5*3600: await delete_call(guild,row); continue
                empty_since=row['created'] if not voice.members else None
                with db() as c:
                    stored=c.execute('SELECT empty_since FROM calls WHERE voice=?',(row['voice'],)).fetchone()
                    if voice.members: c.execute('UPDATE calls SET empty_since=NULL WHERE voice=?',(row['voice'],))
                    elif stored and stored[0] is None: c.execute('UPDATE calls SET empty_since=? WHERE voice=?',(now,row['voice']))
                    elif stored and stored[0] and now-stored[0]>=300: await delete_call(guild,row)
        except Exception as e: print('Call cleanup:',e)
        await asyncio.sleep(30)

@bot.tree.command(name='config-call',description='Configura tamanhos, imagem e limites das calls')
async def config_call(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
    await i.response.send_modal(CallConfigModal())
@bot.tree.command(name='config-call-intervalo',description='Define intervalo em horas entre calls do mesmo usuário')
async def config_call_intervalo(i,horas:app_commands.Range[float,0,168]):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
    cfg=get(i.guild.id,'call_config',{'title':'Criar sua call','description':'Selecione o tamanho e escolha pública ou privada.','sizes':[2,4,6,10],'max':5,'image':''})
    cfg['cooldown']=horas; put(i.guild.id,'call_config',cfg)
    await i.response.send_message(f'Intervalo salvo: {horas} hora(s).',ephemeral=True)
@bot.tree.command(name='enviar-painel-call',description='Publica o painel de criação de calls neste canal')
async def enviar_painel_call(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono publica.',ephemeral=True)
    await i.response.defer(ephemeral=True,thinking=True)
    cfg=get(i.guild.id,'call_config',{'title':'Criar sua call','description':'Selecione o tamanho e escolha pública ou privada.','image':''})
    embed=discord.Embed(title=cfg['title'],description=cfg['description'],color=BRAND_BLUE)
    if cfg.get('image'): embed.set_image(url=cfg['image'])
    await i.channel.send(embed=embed,view=CallPanel(i.guild)); await i.followup.send('Painel de calls enviado.',ephemeral=True)

# Moderação: o bot só denuncia; a punição depende do clique do dono.
class ModConfigModal(discord.ui.Modal,title='Configurar anti-flood e palavras'):
    palavras=discord.ui.TextInput(label='Palavras proibidas, separadas por vírgula',style=discord.TextStyle.paragraph,required=False,max_length=1500)
    async def on_submit(self,i):
        if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
        words=[w.strip().casefold() for w in str(self.palavras).split(',') if w.strip()]
        put(i.guild.id,'blocked_words',words); await i.response.send_message('Filtro de palavras salvo. Configure os três canais com /config-moderacao-canais.',ephemeral=True)
class ModActions(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    async def report(self,i):
        with db() as c: return c.execute('SELECT * FROM mod_reports WHERE message=?',(i.message.id,)).fetchone()
    @discord.ui.button(label='Aplicar punição',style=discord.ButtonStyle.danger,custom_id='mod:punish')
    async def punish(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode aprovar a punição.',ephemeral=True)
        row=await self.report(i)
        if not row: return await i.response.send_message('Alerta não encontrado.',ephemeral=True)
        guild=i.guild; member=guild.get_member(row['user']) if guild else None
        if not member: return await i.response.send_message('A pessoa não está mais no servidor.',ephemeral=True)
        with db() as c:
            old=c.execute('SELECT count FROM warn_counts WHERE guild=? AND user=?',(row['guild'],row['user'])).fetchone()
            count=(old['count'] if old else 0)+1
        punishment=''
        try:
            if count==1: await member.timeout(timedelta(minutes=10),reason='1º aviso aprovado pelo dono'); punishment='10 minutos'
            elif count==2: await member.timeout(timedelta(days=1),reason='2º aviso aprovado pelo dono'); punishment='1 dia'
            elif get(row['guild'],'third_action','7d')=='ban': await guild.ban(member,reason='3º aviso aprovado pelo dono'); punishment='banimento'
            else: await member.timeout(timedelta(days=7),reason='3º aviso aprovado pelo dono'); punishment='7 dias'
        except discord.Forbidden: return await i.response.send_message('O bot não tem permissão ou o cargo da pessoa está acima do cargo dele.',ephemeral=True)
        with db() as c: c.execute('INSERT INTO warn_counts VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET count=excluded.count',(row['guild'],row['user'],count))
        text=f'Você recebeu o aviso {count}/3 em {guild.name}. Motivo: {row["reason"]}. Mensagem: “{row["content"][:700]}”. Punição: {punishment}.'
        try: await member.send(text)
        except discord.Forbidden: pass
        with db() as c: c.execute('UPDATE mod_reports SET state="applied" WHERE message=?',(i.message.id,))
        await i.response.edit_message(content=f'Punição aplicada ({punishment}); DM enviada quando disponível.',view=None)
    @discord.ui.button(label='Ignorar',style=discord.ButtonStyle.secondary,custom_id='mod:ignore')
    async def ignore(self,i,b):
        if not owner(i): return await i.response.send_message('Só o dono pode ignorar este alerta.',ephemeral=True)
        with db() as c: c.execute('UPDATE mod_reports SET state="ignored" WHERE message=?',(i.message.id,))
        await i.response.edit_message(content='Alerta ignorado; nenhuma punição foi aplicada.',view=None)

@bot.tree.command(name='config-moderacao',description='Configura palavras que serão sinalizadas para revisão')
async def config_moderacao(i):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
    await i.response.send_modal(ModConfigModal())
@bot.tree.command(name='config-moderacao-canais',description='Define canais privados dos alertas 1, 2 e 3')
async def config_moderacao_canais(i,aviso1:discord.TextChannel,aviso2:discord.TextChannel,aviso3:discord.TextChannel,terceiro:str='7d'):
    if not i.guild or not owner(i): return await i.response.send_message('Somente o dono configura.',ephemeral=True)
    if terceiro not in ('7d','ban'): return await i.response.send_message('A terceira punição deve ser 7d ou ban.',ephemeral=True)
    put(i.guild.id,'mod_channels',[aviso1.id,aviso2.id,aviso3.id]); put(i.guild.id,'third_action',terceiro)
    await i.response.send_message('Canais de revisão e punição do terceiro aviso salvos.',ephemeral=True)

_recent_messages={}; _mod_report_cooldown={}
@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user and bot.user in message.mentions:
        await message.reply(f'{em_text("ajuda")} Olá! Use /ajuda ou ?ajuda para ver os comandos.',mention_author=False)
    if message.guild and not owner_for_member(message.guild,message.author):
        now=time.monotonic(); key=(message.guild.id,message.author.id)
        hits=[t for t in _recent_messages.get(key,[]) if now-t<8]; hits.append(now); _recent_messages[key]=hits
        reason=None
        if len(hits)>=5: reason='Possível flood: 5 mensagens em 8 segundos'
        text=message.content.casefold()
        words=get(message.guild.id,'blocked_words',[])
        found=next((w for w in words if w and w in text),None)
        if found: reason=f'Palavra sinalizada: {found}'
        if reason:
            last=_mod_report_cooldown.get(key,0)
            if now-last>60:
                _mod_report_cooldown[key]=now
                await send_mod_report(message,reason)
    await bot.process_commands(message)
def owner_for_member(guild,member):
    if member.id==guild.owner_id or (OWNER_ID and member.id==OWNER_ID): return True
    rid=get(guild.id,'owner_role'); sr=get(guild.id,'staff_role')
    return any(r.id in (rid,sr) for r in getattr(member,'roles',[]))
async def send_mod_report(message,reason):
    channels=get(message.guild.id,'mod_channels',[])
    with db() as c: old=c.execute('SELECT count FROM warn_counts WHERE guild=? AND user=?',(message.guild.id,message.author.id)).fetchone()
    level=min((old['count'] if old else 0)+1,3)
    channel=message.guild.get_channel(channels[level-1]) if len(channels)>=level else None
    if not isinstance(channel,discord.TextChannel): return
    embed=discord.Embed(title=f'Análise de moderação · Aviso {level}',description=f'Pessoa: {message.author.mention}\nMotivo: {reason}\nMensagem: “{message.content[:1000]}”\nCanal original: {message.channel.mention}',color=BRAND_BLUE)
    embed.set_footer(text=f'mod-report:{message.guild.id}:{message.author.id}:{level}:{reason[:80]}')
    alert=await channel.send(embed=embed,view=ModActions())
    with db() as c: c.execute('INSERT OR REPLACE INTO mod_reports(message,guild,user,channel,content,reason,level,created,state) VALUES(?,?,?,?,?,?,?,?,?)',(alert.id,message.guild.id,message.author.id,message.channel.id,message.content[:1500],reason,level,time.time(),'pending'))

@bot.tree.command(name='ajuda',description='Mostra os comandos do bot')
async def ajuda(i):
    txt='**Tickets:** /config-dono, /config-ticket\n**KEYS:** /config-keys, /enviar-painel-keys, /gerar-key, /dar-key, /inspecionar-key, /config-regras-key, /config-key-gerar\n**Calls:** /config-call, /enviar-painel-call\n**Moderação:** /config-moderacao, /config-moderacao-canais\nPrefixo: ?ajuda. Mencione o bot para receber esta ajuda.'
    await i.response.send_message(embed=discord.Embed(title='Ajuda',description=txt,color=BRAND_BLUE),ephemeral=True)
@bot.command(name='ajuda')
async def ajuda_prefix(ctx): await ctx.reply('Use /ajuda para ver os comandos.',mention_author=False)


@bot.event
async def on_ready():
    print(f'Bot online: {bot.user} | comandos sincronizados.')

if not TOKEN:
    raise SystemExit('Preencha DISCORD_TOKEN no arquivo .env antes de iniciar.')
if not SUPA:
    print('AVISO: Supabase offline. KEYs e o restante do site exigem SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY.')
bot.run(TOKEN)
