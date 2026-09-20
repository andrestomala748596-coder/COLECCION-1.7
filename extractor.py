import subprocess
import os
import json
import re
import time
import requests
import html
from urllib.parse import urlparse

# ============================================================
# EXTRACTOR VIMEOS (CORREGIDO - SOLO .net)
# ============================================================

def get_direct_url_vimeos(url):
    """Extrae la URL m3u8 de Vimeos - SOLO .net, excluye .zip"""
    try:
        if '?' in url:
            url = url.split('?', 1)[0]

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        # Timeout reducido para Actions
        res = requests.get(url, headers=headers, timeout=15)

        scripts = re.findall(r'<script[^>]*>(.*?)</script>', res.text, re.DOTALL | re.IGNORECASE)
        packed_script = ""
        for s in scripts:
            if "eval(function(p,a,c,k,e,d)" in s and "jw" in s:
                packed_script = s
                break

        if not packed_script:
            return None

        match = re.search(r"function\(p,a,c,k,e,d\)\{.*?\}\(('.+?'),\s*(\d+),\s*(\d+),\s*('.+?')\.split\('\|'\)", packed_script, re.DOTALL)

        if not match:
            return None

        p, a, c, k_str = match.group(1).strip("'"), int(match.group(2)), int(match.group(3)), match.group(4).strip("'").split('|')

        def baseN(num, b, numerals="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            return ((num == 0) and numerals[0]) or (baseN(num // b, b, numerals).lstrip(numerals[0]) + numerals[num % b])

        while c > 0:
            c -= 1
            if k_str[c]:
                word = baseN(c, a)
                p = re.sub(r'\b' + re.escape(word) + r'\b', k_str[c], p)

        todas_urls = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', p)
        
        for u in todas_urls:
            if '.net' in u:
                return u
        
        return None

    except Exception as e:
        return None


# ============================================================
# NUEVA FUNCIÓN PARA OK.RU (OPTIMIZADA PARA ACTIONS)
# ============================================================

def get_direct_url_okru_html(url):
    """Extrae la URL de MAYOR CALIDAD directamente del HTML de OK.ru"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Referer': 'https://ok.ru/'
        }

        # Timeout corto para no colgar Actions
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            return None

        html_text = resp.text
        html_text = html.unescape(html_text)
        html_text = html_text.replace(r"\/", "/")
        html_text = html_text.replace(r"\u0026", "&")

        match_meta = re.search(r'"metadata"\s*:\s*\{', html_text, re.I)
        if not match_meta:
            return None

        start = match_meta.end() - 1
        depth = 0
        dentro_string = False
        escape = False
        end = -1

        # Extracción de objeto balanceado respetando strings y escapes
        for i in range(start, len(html_text)):
            c = html_text[i]

            if dentro_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    dentro_string = False
                continue

            if c == '"':
                dentro_string = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            return None

        json_str = html_text[start:end + 1]
        try:
            data = json.loads(json_str)
        except Exception:
            # Intento con limpieza de trailing commas
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", json_str))
            except Exception:
                return None

        videos = data.get("videos", [])
        if not videos:
            return None

        # --- Tabla de niveles por nombre (equivalente al `type` de OK.ru) ---
        NIVELES_POR_NOMBRE = {
            "ultra": 7, "2160": 7, "4k": 7,
            "quadhd": 6, "1440": 6,
            "full_hd": 5, "fullhd": 5, "1080": 5,
            "hd": 4, "720": 4,
            "sd": 3, "480": 3,
            "low": 2, "360": 2,
            "lowest": 1, "240": 1,
            "mobile": 0, "144": 0,
        }

        def _numero(v):
            try:
                return float(v)
            except Exception:
                return 0.0

        def _nivel_por_nombre(nombre):
            if not isinstance(nombre, str):
                return None
            nombre = nombre.lower()
            for clave, nivel in NIVELES_POR_NOMBRE.items():
                if clave in nombre:
                    return nivel
            return None

        mejor_url = None
        max_score = float("-inf")

        for v in videos:
            if not isinstance(v, dict):
                continue

            url_vid = v.get("url")
            if not url_vid or not isinstance(url_vid, str):
                continue

            url_vid = url_vid.strip()
            if not url_vid.startswith(("http://", "https://")):
                continue

            width = _numero(v.get("width"))
            height = _numero(v.get("height"))
            bitrate = _numero(v.get("bitrate"))

            # 1) Resolución real (criterio principal)
            score = (width * height) * 1000.0

            # 2) Type como refuerzo
            tipo_num = None
            tipo_raw = v.get("type")
            try:
                if tipo_raw is not None and tipo_raw != "":
                    tipo_num = int(tipo_raw)
            except (ValueError, TypeError):
                tipo_num = None

            if tipo_num is not None:
                score += tipo_num * 1_000_000.0
            else:
                # 3) Fallback por nombre solo si no hay type
                nivel = _nivel_por_nombre(v.get("name"))
                if nivel is not None:
                    score += nivel * 1_000_000.0

            # 4) Desempate por bitrate
            score += bitrate

            if score > max_score:
                max_score = score
                mejor_url = url_vid

        return mejor_url

    except Exception:
        return None


# ============================================================
# EXTRACTOR YT-DLP
# ============================================================

def get_direct_url_ytdlp(video_url):
    try:
        video_url = video_url.strip()
        if video_url.startswith('//'):
            video_url = 'https:' + video_url
        elif not video_url.startswith(('http://', 'https://')):
            video_url = 'https://' + video_url
        
        plataforma = None
        if 'vkvideo.ru' in video_url or 'vk.com' in video_url:
            plataforma = 'vkvideo.ru'
        elif 'videa.hu' in video_url:
            plataforma = 'videa.hu'
        elif 'tokyvideo.com' in video_url:
            plataforma = 'tokyvideo.com'
        
        if not plataforma:
            return None
        
        cmd = ['yt-dlp', '-g', video_url]
        # Timeout en subprocess para Actions
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        
        return None
        
    except Exception as e:
        return None


# ============================================================
# EXTRACTOR UNIFICADO
# ============================================================

def get_direct_url(video_url):
    if not video_url:
        return None

    video_url = video_url.strip()
    if video_url.startswith('//'):
        video_url = 'https:' + video_url
    elif not video_url.startswith(('http://', 'https://')):
        video_url = 'https://' + video_url

    url_lower = video_url.lower()

    if 'vimeos.net' in url_lower:
        return get_direct_url_vimeos(video_url)

    elif 'ok.ru' in url_lower:
        return get_direct_url_okru_html(video_url)

    elif any(x in url_lower for x in ['vkvideo.ru', 'vk.com', 'videa.hu', 'tokyvideo.com']):
        return get_direct_url_ytdlp(video_url)

    else:
        return None


# ============================================================
# PROCESAR PELÍCULA
# ============================================================

def procesar_pelicula(pelicula, idx, total):
    titulo = pelicula.get('TITULO', f'Película {idx+1}')
    urls = pelicula.get('URLS', pelicula.get('URLS_OKRU', []))
    
    if not urls:
        return pelicula
    
    urls_directas = []
    for url_video in urls:
        url_directa = get_direct_url(url_video)
        if url_directa:
            urls_directas.append(url_directa)
        # Pequeña pausa para no saturar
        time.sleep(0.5)
    
    if urls_directas:
        pelicula['URLS_DIRECTAS'] = urls_directas
        pelicula['URL_DIRECTA'] = urls_directas[0]
    else:
        pelicula['URLS_DIRECTAS'] = []
        pelicula['URL_DIRECTA'] = ""
    
    return pelicula


# ============================================================
# MAIN
# ============================================================

def main():
    print("🚀 Iniciando extractor...")
    
    if not os.path.exists('urls.txt'):
        print("❌ urls.txt no encontrado")
        return
    
    try:
        with open('urls.txt', 'r', encoding='utf-8') as f:
            contenido = f.read().strip()
        
        if not contenido or contenido == "[]":
            print("ℹ️ urls.txt vacío")
            return
        
        peliculas = json.loads(contenido)
    except Exception as e:
        print(f"❌ Error leyendo urls.txt: {e}")
        return
    
    print(f"📥 {len(peliculas)} películas encontradas")
    
    os.makedirs('peliculas', exist_ok=True)
    
    for i, pelicula in enumerate(peliculas):
        print(f"Procesando {i+1}/{len(peliculas)}: {pelicula.get('TITULO', 'Unknown')}")
        peliculas[i] = procesar_pelicula(pelicula, i, len(peliculas))
        
        # Guardar progreso parcial por seguridad
        with open('urls.txt', 'w', encoding='utf-8') as f:
            json.dump(peliculas, f, indent=2, ensure_ascii=False)
    
    # ORGANIZAR POR CATEGORÍA
    categorias_dict = {}
    for pelicula in peliculas:
        categoria = pelicula.get('CATEGORIA', 'GENERAL').upper()
        if categoria not in categorias_dict:
            categorias_dict[categoria] = []
        categorias_dict[categoria].append(pelicula)
    
    for categoria, pelis in categorias_dict.items():
        json_path = os.path.join('peliculas', f'{categoria}.json')
        data = []
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                data = []
        
        for pelicula in pelis:
            id_busqueda = pelicula.get('ID_VIDEO') or pelicula.get('ID_OKRU') or pelicula.get('TMDB_ID')
            encontrado = False
            for j, item in enumerate(data):
                item_id = item.get('ID_VIDEO') or item.get('ID_OKRU') or item.get('TMDB_ID')
                if item_id == id_busqueda or item.get('TMDB_ID') == pelicula.get('TMDB_ID'):
                    data[j] = pelicula
                    encontrado = True
                    break
            if not encontrado:
                data.append(pelicula)
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    with open('category_list.json', 'w', encoding='utf-8') as f:
        json.dump(list(categorias_dict.keys()), f, indent=2, ensure_ascii=False)
    
    print("✅ Proceso completado")


if __name__ == "__main__":
    main()
