### Получение токена
Для получения токенов надо воспользоваться проектом  
https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor

### Запуск контейнера

Создать devices.json в configs папке
```json
[
    {
        "device_name": "Humidifier",
        "ip": "192.168.87.181",
        "token": "0436123bbc911e48c340acac04c3bd08",
        "type": "zhimi.humidifier.ca4"
    }
]
```
Запустить докер контейнер и пробросить в него файл devices.json (см компоуз файл)

### meta_topics.json
Файл, который содержит данные для мета топиков устройства. В моем случае это увлажнитель воздуха.

### Сборка контейнера
#### Настройка кросс компиляции
```bash
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create \
  --name container-builder \
  --driver docker-container \
  --bootstrap --use
```

#### Сборка контейнера
Для wirenboard 7 кросскомпиляция на ноутбуке
```bash
docker buildx build --tag hum2:latest --platform linux/arm/v7 --load .
```

Для raspberry pi сборка на ней же
```bash
docker buildx build --tag hum2:latest --load .
```
