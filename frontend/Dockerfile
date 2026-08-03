FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install
COPY . .

# NEXT_PUBLIC_BACKEND_URL must be baked into the client bundle at build
# time (Next.js inlines NEXT_PUBLIC_* at build time). The default value
# is used when Render doesn't pass the build arg — which can happen on
# the first Blueprint deploy before the env var is fully resolved.
# docker-compose dev flow is unchanged (compose overrides CMD with
# `npm run dev`, which reads the var at runtime).
ARG NEXT_PUBLIC_BACKEND_URL=https://tradewind-backend.onrender.com
ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL

# Next.js builds are memory-hungry. The free plan's "starter" build tier
# (~512 MB) can OOM-kill the build. Limiting the Node heap keeps it alive.
ENV NODE_OPTIONS="--max-old-space-size=384"

# Disable telemetry and skip lint in production build (lint is a dev concern).
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
