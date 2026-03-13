#!/usr/bin/env python3
import subprocess
import sys
import pkg_resources
import os

# ================ АВТОМАТИЧЕСКАЯ УСТАНОВКА ЗАВИСИМОСТЕЙ ================
required_packages = {
    'discord': 'discord.py[voice]>=2.3.0',
    'yt_dlp': 'yt-dlp>=2024.7.9',
    'dotenv': 'python-dotenv>=1.0.0',
    'nacl': 'PyNaCl>=1.5.0'
}

def install_package(package):
    """Установка пакета"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package])
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка установки {package}: {e}")
        return False

print("🔍 Проверка зависимостей...")
for package_name, package_spec in required_packages.items():
    try:
        pkg_resources.get_distribution(package_name.replace('_', '-'))
        print(f"✅ {package_name} уже установлен")
    except pkg_resources.DistributionNotFound:
        print(f"📦 Установка {package_name}...")
        if not install_package(package_spec):
            print(f"❌ Критическая ошибка: не удалось установить {package_name}")
            sys.exit(1)

print("=" * 50)
# =========================================================================

# Теперь импортируем все необходимое
import discord
from discord.ext import commands
import yt_dlp
import asyncio
from collections import deque
import re
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# ================ НАСТРОЙКИ ================
TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = '!'
DEFAULT_VOLUME = 50
MAX_QUEUE_SIZE = 100

# Цвета для Embed сообщений
COLORS = {
    'play': 0x00ff00,
    'queue': 0x3498db,
    'error': 0xff0000,
    'info': 0xf1c40f,
    'success': 0x2ecc71
}
# ============================================

# Настройки для yt-dlp (поддержка YouTube и SoundCloud)
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'extractors': ['youtube', 'soundcloud'],
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

# Проверяем наличие yt-dlp и создаем экземпляр
try:
    ydl = yt_dlp.YoutubeDL(ytdl_format_options)
    print("✅ yt-dlp успешно инициализирован")
except Exception as e:
    print(f"❌ Ошибка инициализации yt-dlp: {e}")
    sys.exit(1)

# ================ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ================
def format_duration(seconds):
    """Форматирование длительности трека"""
    if not seconds:
        return "Неизвестно"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"

def get_platform_from_url(url):
    """Определение платформы по URL"""
    youtube_patterns = [
        r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/',
        r'(https?://)?(m\.)?youtube\.com/',
        r'(https?://)?(www\.)?youtube\.com/shorts/'
    ]
    
    soundcloud_patterns = [
        r'(https?://)?(www\.)?soundcloud\.com/',
        r'(https?://)?(m\.)?soundcloud\.com/'
    ]
    
    for pattern in youtube_patterns:
        if re.match(pattern, url):
            return 'youtube'
    
    for pattern in soundcloud_patterns:
        if re.match(pattern, url):
            return 'soundcloud'
    
    return 'unknown'

def create_progress_bar(current, total, length=20):
    """Создание прогресс-бара"""
    if total == 0:
        return "▬" * length
    
    progress = int((current / total) * length)
    bar = "█" * progress + "▬" * (length - progress)
    return bar
# =========================================================

class YTDLSource(discord.PCMVolumeTransformer):
    """Класс для загрузки и воспроизведения аудио"""
    
    def __init__(self, source, *, data, volume=DEFAULT_VOLUME/100):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Неизвестно')
        self.url = data.get('webpage_url', data.get('url', ''))
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.platform = data.get('extractor', 'unknown')
        self.uploader = data.get('uploader', data.get('channel', 'Неизвестно'))
        self.likes = data.get('like_count', 0)
        self.views = data.get('view_count', 0)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        """Загрузка аудио по URL или поисковому запросу"""
        loop = loop or asyncio.get_event_loop()
        
        try:
            data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=not stream))
            
            if data is None:
                raise Exception("Не удалось получить информацию о треке")
            
            if 'entries' in data:
                # Это плейлист, берем первый трек
                data = data['entries'][0]
            
            filename = data['url'] if stream else ydl.prepare_filename(data)
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
            
        except Exception as e:
            raise Exception(f"Ошибка загрузки: {str(e)}")

class MusicBot(commands.Bot):
    """Основной класс бота"""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        
        super().__init__(command_prefix=PREFIX, intents=intents)
        
        # Хранилища данных для каждого сервера
        self.voice_clients = {}
        self.queues = {}
        self.now_playing = {}
        self.volume_levels = {}

    async def setup_hook(self):
        """Добавление когов при запуске"""
        await self.add_cog(MusicCommands(self))

class MusicCommands(commands.Cog):
    """Класс с музыкальными командами"""
    
    def __init__(self, bot):
        self.bot = bot

    def get_queue(self, guild_id):
        """Получение очереди для сервера"""
        if guild_id not in self.bot.queues:
            self.bot.queues[guild_id] = deque(maxlen=MAX_QUEUE_SIZE)
        return self.bot.queues[guild_id]

    @commands.command(name='join', aliases=['connect', 'j'])
    async def join(self, ctx):
        """Подключение к голосовому каналу"""
        if not ctx.author.voice:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Вы не находитесь в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        channel = ctx.author.voice.channel
        
        if ctx.guild.id in self.bot.voice_clients:
            await self.bot.voice_clients[ctx.guild.id].move_to(channel)
        else:
            self.bot.voice_clients[ctx.guild.id] = await channel.connect()
        
        embed = discord.Embed(
            title="✅ Подключение",
            description=f"Подключился к {channel.mention}",
            color=COLORS['success']
        )
        await ctx.send(embed=embed)

    @commands.command(name='play', aliases=['p'])
    async def play(self, ctx, *, query):
        """Воспроизведение музыки с YouTube или SoundCloud"""
        # Проверка подключения
        if not ctx.author.voice:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Вы не находитесь в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        # Определяем платформу
        platform = get_platform_from_url(query)
        platform_emoji = {
            'youtube': '📺',
            'soundcloud': '🎧',
            'unknown': '🎵'
        }.get(platform, '🎵')
        
        # Подключение если не подключены
        if ctx.guild.id not in self.bot.voice_clients:
            await self.join(ctx)
        
        voice_client = self.bot.voice_clients[ctx.guild.id]
        queue = self.get_queue(ctx.guild.id)
        
        # Отправляем сообщение о поиске
        searching_embed = discord.Embed(
            title=f"{platform_emoji} Поиск...",
            description=f"Ищем: **{query}**",
            color=COLORS['info']
        )
        searching_msg = await ctx.send(embed=searching_embed)
        
        try:
            # Поиск и добавление в очередь
            async with ctx.typing():
                player = await YTDLSource.from_url(query, loop=self.bot.loop)
                
                # Добавление в очередь
                queue.append(player)
                
                # Определяем название платформы
                platform_names = {
                    'youtube': 'YouTube',
                    'soundcloud': 'SoundCloud',
                    'youtube:tab': 'YouTube',
                    'soundcloud:track': 'SoundCloud'
                }
                platform_name = platform_names.get(player.platform, 'Неизвестно')
                
                # Создаем embed с информацией о треке
                embed = discord.Embed(
                    title="✅ Добавлено в очередь",
                    description=f"**[{player.title}]({player.url})**",
                    color=COLORS['play']
                )
                
                if player.thumbnail:
                    embed.set_thumbnail(url=player.thumbnail)
                
                embed.add_field(name="Платформа", value=f"{platform_emoji} {platform_name}", inline=True)
                embed.add_field(name="Длительность", value=f"`{format_duration(player.duration)}`", inline=True)
                embed.add_field(name="Автор", value=player.uploader[:50], inline=True)
                embed.add_field(name="Позиция", value=f"`{len(queue)}`", inline=True)
                
                if player.likes > 0:
                    embed.add_field(name="❤️ Лайки", value=f"{player.likes:,}", inline=True)
                if player.views > 0:
                    embed.add_field(name="👁️ Просмотры", value=f"{player.views:,}", inline=True)
                
                # Если ничего не играет, начинаем воспроизведение
                if not voice_client.is_playing():
                    await searching_msg.delete()
                    await self.play_next(ctx)
                else:
                    await searching_msg.edit(embed=embed)
                    
        except Exception as e:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Не удалось воспроизвести: {str(e)}",
                color=COLORS['error']
            )
            await searching_msg.edit(embed=embed)

    async def play_next(self, ctx):
        """Воспроизведение следующего трека в очереди"""
        queue = self.get_queue(ctx.guild.id)
        
        if not queue:
            if ctx.guild.id in self.bot.now_playing:
                del self.bot.now_playing[ctx.guild.id]
            return
        
        voice_client = self.bot.voice_clients[ctx.guild.id]
        player = queue.popleft()
        
        self.bot.now_playing[ctx.guild.id] = player
        
        # Устанавливаем громкость
        if ctx.guild.id in self.bot.volume_levels:
            player.volume = self.bot.volume_levels[ctx.guild.id] / 100
        else:
            player.volume = DEFAULT_VOLUME / 100
            self.bot.volume_levels[ctx.guild.id] = DEFAULT_VOLUME
        
        # Определяем платформу для эмодзи
        platform_emoji = {
            'youtube': '📺',
            'soundcloud': '🎧'
        }.get(player.platform, '🎵')
        
        # Отправка сообщения о текущем треке
        embed = discord.Embed(
            title=f"{platform_emoji} Сейчас играет",
            description=f"**[{player.title}]({player.url})**",
            color=COLORS['play']
        )
        
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        
        embed.add_field(name="Длительность", value=f"`{format_duration(player.duration)}`", inline=True)
        embed.add_field(name="Автор", value=player.uploader[:50], inline=True)
        embed.add_field(name="Громкость", value=f"`{self.bot.volume_levels[ctx.guild.id]}%`", inline=True)
        
        await ctx.send(embed=embed)
        
        voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(
            self.play_next(ctx), self.bot.loop
        ))

    @commands.command(name='skip', aliases=['next', 's'])
    async def skip(self, ctx):
        """Пропустить текущий трек"""
        voice_client = self.bot.voice_clients.get(ctx.guild.id)
        
        if not voice_client:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Бот не в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        if voice_client.is_playing():
            voice_client.stop()
            embed = discord.Embed(
                title="⏭️ Пропущено",
                description="Трек пропущен",
                color=COLORS['info']
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Сейчас ничего не играет",
                color=COLORS['error']
            )
            await ctx.send(embed=embed)

    @commands.command(name='queue', aliases=['q'])
    async def show_queue(self, ctx):
        """Показать очередь треков"""
        queue = self.get_queue(ctx.guild.id)
        
        embed = discord.Embed(
            title="📋 Очередь треков",
            color=COLORS['queue']
        )
        
        # Текущий трек
        if ctx.guild.id in self.bot.now_playing:
            current = self.bot.now_playing[ctx.guild.id]
            embed.add_field(
                name="🎵 Сейчас играет",
                value=f"**[{current.title}]({current.url})**\n"
                      f"Длительность: `{format_duration(current.duration)}`",
                inline=False
            )
        
        # Очередь
        if queue:
            queue_text = ""
            for i, track in enumerate(list(queue)[:10], 1):
                queue_text += f"`{i}.` **[{track.title}]({track.url})** `{format_duration(track.duration)}`\n"
            
            if len(queue) > 10:
                queue_text += f"\n...и еще {len(queue) - 10} треков"
            
            embed.add_field(name=f"В очереди ({len(queue)})", value=queue_text, inline=False)
        else:
            embed.add_field(name="В очереди", value="Пусто", inline=False)
        
        await ctx.send(embed=embed)

    @commands.command(name='pause')
    async def pause(self, ctx):
        """Поставить на паузу"""
        voice_client = self.bot.voice_clients.get(ctx.guild.id)
        
        if not voice_client:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Бот не в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        if voice_client.is_playing():
            voice_client.pause()
            embed = discord.Embed(
                title="⏸️ Пауза",
                description="Воспроизведение приостановлено",
                color=COLORS['info']
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Сейчас ничего не играет или уже на паузе",
                color=COLORS['error']
            )
            await ctx.send(embed=embed)

    @commands.command(name='resume', aliases=['continue'])
    async def resume(self, ctx):
        """Возобновить воспроизведение"""
        voice_client = self.bot.voice_clients.get(ctx.guild.id)
        
        if not voice_client:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Бот не в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        if voice_client.is_paused():
            voice_client.resume()
            embed = discord.Embed(
                title="▶️ Возобновлено",
                description="Воспроизведение продолжено",
                color=COLORS['success']
            )
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Бот не на паузе",
                color=COLORS['error']
            )
            await ctx.send(embed=embed)

    @commands.command(name='stop', aliases=['leave', 'disconnect'])
    async def stop(self, ctx):
        """Остановить и отключиться"""
        voice_client = self.bot.voice_clients.get(ctx.guild.id)
        
        if not voice_client:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Бот не в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        # Очистка очереди
        if ctx.guild.id in self.bot.queues:
            self.bot.queues[ctx.guild.id].clear()
        
        if voice_client.is_playing():
            voice_client.stop()
        
        await voice_client.disconnect()
        if ctx.guild.id in self.bot.voice_clients:
            del self.bot.voice_clients[ctx.guild.id]
        
        embed = discord.Embed(
            title="👋 Отключение",
            description="Бот отключился от голосового канала",
            color=COLORS['info']
        )
        await ctx.send(embed=embed)

    @commands.command(name='volume', aliases=['vol'])
    async def change_volume(self, ctx, volume: int = None):
        """Изменить громкость (0-100)"""
        voice_client = self.bot.voice_clients.get(ctx.guild.id)
        
        if not voice_client:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Бот не в голосовом канале!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        if volume is None:
            current_vol = self.bot.volume_levels.get(ctx.guild.id, DEFAULT_VOLUME)
            embed = discord.Embed(
                title="🔊 Текущая громкость",
                description=f"**{current_vol}%**",
                color=COLORS['info']
            )
            return await ctx.send(embed=embed)
        
        if volume < 0 or volume > 100:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Громкость должна быть от 0 до 100!",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        self.bot.volume_levels[ctx.guild.id] = volume
        
        if voice_client.source and hasattr(voice_client.source, 'volume'):
            voice_client.source.volume = volume / 100
            
            embed = discord.Embed(
                title="🔊 Громкость изменена",
                description=f"Установлена на **{volume}%**",
                color=COLORS['success']
            )
            await ctx.send(embed=embed)

    @commands.command(name='np', aliases=['nowplaying', 'current'])
    async def now_playing(self, ctx):
        """Показать текущий трек"""
        if ctx.guild.id not in self.bot.now_playing:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Сейчас ничего не играет",
                color=COLORS['error']
            )
            return await ctx.send(embed=embed)
        
        player = self.bot.now_playing[ctx.guild.id]
        voice_client = self.bot.voice_clients.get(ctx.guild.id)
        
        platform_emoji = {
            'youtube': '📺',
            'soundcloud': '🎧'
        }.get(player.platform, '🎵')
        
        embed = discord.Embed(
            title=f"{platform_emoji} Сейчас играет",
            description=f"**[{player.title}]({player.url})**",
            color=COLORS['play']
        )
        
        if player.thumbnail:
            embed.set_thumbnail(url=player.thumbnail)
        
        embed.add_field(name="Длительность", value=f"`{format_duration(player.duration)}`", inline=True)
        embed.add_field(name="Автор", value=player.uploader[:50], inline=True)
        
        if voice_client and voice_client.is_playing():
            # Прогресс-бар
            position = voice_client.position
            if position:
                progress = create_progress_bar(position, player.duration)
                embed.add_field(
                    name="Прогресс",
                    value=f"{progress} `{format_duration(position)}/{format_duration(player.duration)}`",
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @commands.command(name='clear')
    async def clear_queue(self, ctx):
        """Очистить очередь"""
        if ctx.guild.id in self.bot.queues and self.bot.queues[ctx.guild.id]:
            self.bot.queues[ctx.guild.id].clear()
            embed = discord.Embed(
                title="🧹 Очередь очищена",
                description="Все треки удалены из очереди",
                color=COLORS['success']
            )
        else:
            embed = discord.Embed(
                title="📪 Очередь пуста",
                description="Нечего очищать",
                color=COLORS['info']
            )
        
        await ctx.send(embed=embed)

    @commands.command(name='shuffle')
    async def shuffle_queue(self, ctx):
        """Перемешать очередь"""
        if ctx.guild.id in self.bot.queues and len(self.bot.queues[ctx.guild.id]) > 1:
            import random
            queue_list = list(self.bot.queues[ctx.guild.id])
            random.shuffle(queue_list)
            self.bot.queues[ctx.guild.id] = deque(queue_list, maxlen=MAX_QUEUE_SIZE)
            
            embed = discord.Embed(
                title="🔀 Очередь перемешана",
                description=f"Теперь в очереди **{len(queue_list)}** треков",
                color=COLORS['success']
            )
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Недостаточно треков для перемешивания",
                color=COLORS['error']
            )
        
        await ctx.send(embed=embed)

    @commands.command(name='help_music')
    async def music_help(self, ctx):
        """Показать справку по музыкальным командам"""
        embed = discord.Embed(
            title="🎵 Музыкальный бот - Команды",
            description=f"Префикс: `{PREFIX}`\nПоддерживает YouTube и SoundCloud!",
            color=COLORS['info']
        )
        
        embed.add_field(
            name="🎵 Основные команды",
            value=(
                "`play <запрос/URL>` - Воспроизвести музыку\n"
                "`join` - Подключиться к каналу\n"
                "`stop` - Остановить и отключиться\n"
                "`skip` - Пропустить трек\n"
                "`pause` - Пауза\n"
                "`resume` - Продолжить"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📋 Управление очередью",
            value=(
                "`queue` - Показать очередь\n"
                "`clear` - Очистить очередь\n"
                "`shuffle` - Перемешать очередь\n"
                "`np` - Текущий трек"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Настройки",
            value=(
                "`volume [0-100]` - Изменить громкость\n"
                "`help_music` - Эта справка"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📝 Примеры",
            value=(
                "`!play https://www.youtube.com/watch?v=dQw4w9WgXcQ`\n"
                "`!play https://soundcloud.com/artist/track`\n"
                "`!play Imagine Dragons - Believer`"
            ),
            inline=False
        )
        
        embed.set_footer(text="Создано с любовью ❤️")
        
        await ctx.send(embed=embed)

# ================ ЗАПУСК БОТА ================
bot = MusicBot()

@bot.event
async def on_ready():
    """Событие при готовности бота"""
    print(f'✅ Бот {bot.user} успешно запущен!')
    print(f'ID: {bot.user.id}')
    print(f'Префикс команд: {PREFIX}')
    print('Поддерживаемые платформы: YouTube, SoundCloud')
    print('=' * 50)
    
    # Установка статуса
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{PREFIX}help_music | YouTube/SoundCloud"
        )
    )

@bot.event
async def on_command_error(ctx, error):
    """Обработка ошибок команд"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    print(f"Ошибка: {error}")
    
    embed = discord.Embed(
        title="❌ Ошибка",
        description=f"```{error}```",
        color=COLORS['error']
    )
    await ctx.send(embed=embed)

# Проверка токена
if not TOKEN:
    print("❌ Ошибка: Токен не указан!")
    print("Добавьте DISCORD_TOKEN в переменные окружения Bothost")
    print("Или создайте файл .env с токеном")
    exit(1)

print(f"🚀 Запуск бота с токеном: {TOKEN[:10]}...")
print("=" * 50)

# Запуск бота
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("❌ Ошибка: Не удалось войти! Проверьте токен.")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
