FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

RUN npm run build

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
