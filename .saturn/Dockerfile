FROM localhost/saturn-base:latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      jq ripgrep build-essential gh nodejs npm \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
