# tecnogera-portal

> GitHub: https://github.com/willy-digital/tecnogera-portal — API: https://github.com/willy-digital/tecnogera-api

Portal de operação Tecnogera — Vite + React 18 + TypeScript + Tailwind + shadcn/ui.

## Requisitos

- Node.js 20+
- pnpm 11+

## Dev

```bash
pnpm install
pnpm dev          # http://localhost:5173
```

## Build

```bash
pnpm build        # gera dist/
pnpm preview      # serve dist/ localmente
```

## Testes

```bash
pnpm test         # roda vitest em modo CI
pnpm test:watch   # modo watch
```

## Typecheck & Lint

```bash
pnpm typecheck
pnpm lint
pnpm format       # formata com biome
```

## Tipos da API

Gera `src/api/types.ts` a partir do contrato OpenAPI do backend:

```bash
pnpm gen:types                                      # usa http://localhost:8000/openapi.json
OPENAPI_URL=http://api.example.com/openapi.json pnpm gen:types
```

Após mudanças no backend, rode `pnpm gen:types` e comite o diff.
