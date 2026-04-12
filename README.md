# TÜİK SDMX MCP Server

[TÜİK](https://www.tuik.gov.tr/) (Türkiye İstatistik Kurumu) SDMX REST API'sine erişim sağlayan bir [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) sunucusu.

346 dataflow üzerinden (şimdilik) Türkiye'nin resmi istatistiklerine — nüfus, işgücü, enflasyon, dış ticaret, sanayi üretimi vb.
doğrudan LLM üzerinden erişim sağlar.

*SDMX servisi henüz beta aşamasında olduğundan veriler güncel/doğru olmayabilir, zamanla sunucu vb. değişebilir, kodlar bozulabilir. Bu açıdan resmi duyuruyu beklemekte fayda var.*

## Özellikler

- **Guided workflow**: Sunucu, LLM'i adım adım akışı yönlendirir: önce arama, sonra kırılım seçimi, sonra filtreli veri çekme. Token kullanımını minimize eder.
- **Akıllı metadata**: `detail=nodata` ile veri çekmeden boyut yapısını getirir. Tek değerli boyutlar otomatik gizlenir, sadece seçim gerektiren kırılımlar gösterilir.
- **Client-side filtreleme**: TÜİK API'si URL key filter desteklemediğinden, `boyut_filtre` parametresiyle parse sonrası filtreleme yapılır.
- **Otomatik temizlik**: Tek değerli sütunlar (ör. "Not Applicable") veri çıktısından otomatik kaldırılır.

## Kurulum

### Claude Code / Claude Desktop

`settings.json` dosyasına ekleyin:

```json
{
  "mcpServers": {
    "tuik-sdmx": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/orhoncan/tuik-mcp", "tuik-sdmx-mcp", "serve"]
    }
  }
}
```

### Yerel geliştirme

```bash
git clone https://github.com/orhoncan/tuik-mcp.git
cd tuik-mcp
uv sync
uv run tuik-sdmx-mcp serve
```

## Ekran Görüntüleri

<img width="879" height="362" alt="image" src="https://github.com/user-attachments/assets/3a0687db-f04c-4fc1-9919-7a93cc6ef724" />

<img width="585" height="472" alt="image" src="https://github.com/user-attachments/assets/f6bd152c-0148-4d34-a511-82d7c7af3e79" />

<img width="1225" height="451" alt="image" src="https://github.com/user-attachments/assets/95bde6f2-03e9-4a09-9c63-d257da6f7787" />


## Araçlar (Tools)

### `tuik_ara` — Dataflow arama

Anahtar kelimeyle dataflow arar. Dataflow adları İngilizce olduğundan hem Türkçe hem İngilizce terimler deneyin.

```
tuik_ara(query="producer price index")
```

**Dönen:** Eşleşen dataflow'ların listesi (id, name, description, version).

### `tuik_listele` — Tüm dataflow'ları listeleme

(Şu an için) mevcut 346 production dataflow'u listeler.

```
tuik_listele()
tuik_listele(include_test=True)  # test dataflow'ları da dahil
```

### `tuik_meta` — Boyut yapısı

Bir dataflow'un kırılım seçeneklerini getirir. Veri çekmeden önce mutlaka çağırın.

```
tuik_meta(dataflow_id="DF_YIUFE_EDO")
```

**Dönen:**
- `filterable_dimensions`: Birden fazla değeri olan boyutlar (seçim gerektiren)
- `fixed_dimensions`: Tek değerli sabit boyutlar (bilgi amaçlı)

Örnek çıktı:
```json
{
  "filterable_dimensions": [
    {
      "id": "DEGISIM",
      "name": "Change type",
      "values": [
        {"id": "1", "name": "Index"},
        {"id": "2", "name": "Change compared to the previous month (%)"},
        {"id": "4", "name": "Annual rate of change (%)"}
      ],
      "value_count": 5
    },
    {
      "id": "FAAL_GRUP",
      "name": "Activity group",
      "values": [
        {"id": "_T", "name": "Total"},
        {"id": "3", "name": "Section"}
      ],
      "value_count": 6
    }
  ],
  "fixed_dimensions": {
    "REF_AREA": "Türkiye",
    "FREQ": "Monthly"
  }
}
```

### `tuik_cek` — Veri çekme

Tarih aralığı ve boyut filtresiyle veri çeker.

```
tuik_cek(
    dataflow_id="DF_YIUFE_EDO",
    baslangic="2026-01",
    bitis="2026-03",
    boyut_filtre={"FAAL_GRUP": ["Total"], "DEGISIM": ["Index"]}
)
```

**Parametreler:**
| Parametre | Açıklama | Örnek |
|-----------|----------|-------|
| `dataflow_id` | Dataflow ID'si | `"DF_YIUFE_EDO"` |
| `baslangic` | Başlangıç dönemi | `"2024-01"`, `"2023"` |
| `bitis` | Bitiş dönemi | `"2026-03"` |
| `boyut_filtre` | Boyut filtresi (name bazlı) | `{"FAAL_GRUP": ["Total"]}` |

**Dönen:** `{"row_count": 3, "rows": [{...}, ...]}` formatında düz dict listesi.

## Kullanım Akışı

Sunucu, LLM'i şu adımları izlemeye yönlendirir:

```
1. ARAMA          tuik_ara("producer price")
   Kullanıcı:     "DF_YIUFE_EDO olsun"
                   ↓
2. META           tuik_meta("DF_YIUFE_EDO")
   Kullanıcı:     "Toplam endeks, son 3 ay"
                   ↓
3. VERİ ÇEK       tuik_cek("DF_YIUFE_EDO",
                     baslangic="2026-01", bitis="2026-03",
                     boyut_filtre={"FAAL_GRUP": ["Total"], "DEGISIM": ["Index"]})
```

Bu akış sayesinde 25.000+ satırlık ham veri yerine sadece 3 satır döner.

## Geliştirme

```bash
# Testleri çalıştır
uv run python -m pytest tests/ -v

# Sunucuyu başlat
uv run tuik-sdmx-mcp serve

# Sürüm bilgisi
uv run tuik-sdmx-mcp version
```

## Lisans

MIT
