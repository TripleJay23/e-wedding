# Environment Plan

## Environments

### Development

- `APP_ENV=development`
- PostgreSQL database `wedding_ecard_dev`
- private local storage

### Automated/disposable integration test

- `APP_ENV=test`
- unique disposable PostgreSQL database name
- separate private temporary storage
- hard refusal of staging/production database identity

### Staging

- `APP_ENV=staging`
- PostgreSQL `wedding_ecard_staging`
- persistent private staging storage
- continuous Jordan & Jessica acceptance scenario

### Production later

Configured only during deployment milestone.

## Environment files

Commit only examples:

```text
.env.example
backend/.env.example
```

Never commit:

```text
.env
backend/.env
```

## Migration command guard

Before Alembic:

1. print sanitized configured target;
2. connect;
3. query actual DB identity;
4. verify environment/database allow-list;
5. verify current revision;
6. run requested migration.
