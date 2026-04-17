# Architecture Reference

## SQLite Tables

| Table | Key columns |
|---|---|
| `users` | `id`, `username`, `email`, `hashed_password`, `role`, `created_at` |

[Document every table here as it is added. This is the source of truth for the data model.]

---

## InfluxDB Measurements

### `[measurement_name]`
- **Tags**: `[tag1]`, `[tag2]`
- **Fields**: `[field1]`, `[field2]`

[Document every measurement here as it is added.]

---

## API Contracts

### Auth
- `POST /auth/register` — `{username, email, password}` → `{user_id, token}`
- `POST /auth/login` — `{username, password}` → `{access_token, refresh_token}`
- `POST /auth/refresh` — `{refresh_token}` → `{access_token}`

[Add all routes here as they are implemented, grouped by router domain.]

---

## Deployment

### Traefik Label Format

This homelab requires **list format** labels, not map format:

```yaml
# CORRECT
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"
```

When a container is on multiple Docker networks, add `traefik.docker.network=proxy`.

### Healthcheck

`python:3.11-slim` does not include `curl`. Use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Dev Overlay

`docker-compose.dev.yml` extends the base with:
- Live `src/` and `static/` volume mounts (`:ro,z`)
- `proxy` external network + Traefik labels for `[app]-dev.lg4.ch`
- `PYTHONPYCACHEPREFIX=/tmp/pycache` — prevents stale `.pyc` files from shadowing volume-mounted sources
