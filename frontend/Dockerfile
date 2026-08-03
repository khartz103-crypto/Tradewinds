FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .

# Render injects service environment variables as Docker build arguments
# (see render.com/docs/docker — "Environment variable translation"), so
# NEXT_PUBLIC_BACKEND_URL is available here at build time. It must be
# baked in during `next build` because Next.js inlines NEXT_PUBLIC_*
# values into the client bundle at build time.
# NOTE: docker-compose development overrides this image's command with
# `npm run dev`, which reads the variable at runtime instead — so local
# dev behaviour is unchanged.
ARG NEXT_PUBLIC_BACKEND_URL
ENV NEXT_PUBLIC_BACKEND_URL=$NEXT_PUBLIC_BACKEND_URL

RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
