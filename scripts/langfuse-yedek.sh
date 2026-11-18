#!/bin/zsh
# Langfuse kayıtlarını (Postgres + ClickHouse + MinIO volume'leri) tek arşive yedekler.
# Tutarlı kopya için Langfuse birkaç saniye durdurulur; bu sırada gelen izler kaybolur, uygulama etkilenmez.
# Kullanım: scripts/langfuse-yedek.sh            -> ~/Backups/langfuse/langfuse-<tarih>.tgz
# Geri yükleme: compose durdurulur, her volume boşaltılıp arşivdeki aynı adlı klasör içine açılır.
set -euo pipefail

COMPOSE="${LANGFUSE_COMPOSE:-$HOME/Desktop/Project/langfuse/docker-compose.yml}"
HEDEF="${LANGFUSE_YEDEK:-$HOME/Backups/langfuse}"
VOLUMLER=(langfuse_langfuse_postgres_data langfuse_langfuse_clickhouse_data langfuse_langfuse_minio_data)
ARSIV="langfuse-$(date +%Y%m%d-%H%M%S).tgz"

mkdir -p "$HEDEF"
docker compose -f "$COMPOSE" stop >/dev/null
trap 'docker compose -f "$COMPOSE" start >/dev/null' EXIT

baglar=()
for v in $VOLUMLER; do
  docker volume inspect "$v" >/dev/null
  baglar+=(-v "$v:/kaynak/$v:ro")
done
docker run --rm $baglar -v "$HEDEF:/hedef" alpine tar czf "/hedef/$ARSIV" -C /kaynak .

print -r -- "Yedek: $HEDEF/$ARSIV ($(du -h "$HEDEF/$ARSIV" | cut -f1))"
