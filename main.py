import asyncio
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from itertools import takewhile
from typing import List, Any

import aiomqtt
from aiomqtt import Message
from miio import DeviceFactory, AirHumidifierMiot
from miio.integrations.zhimi.humidifier.airhumidifier_miot import OperationMode, LedBrightness


@dataclass
class DeviceParams:
    ip: str
    token: str
    device_name: str
    type: str


class ErrorType(Enum):
    read = 'r'
    write = 'w'


@dataclass
class ErrorState:
    read: bool = True
    write: bool = False


class ErrorManager:
    """
    Класс хранящий состояние ошибки топика в соотвествии с конвенцией
    поддреживает ошибку чтения, записи или их комбинации
    по сути это пара название топика /devices/<device>/controls/<control> и его состояния rw значений
    по умолчанию заполняется ошибкой для чтения, потому что старте топик без значений
    и надо явно помененить что значений нет или они старые
    помечаем ошибкой чтения
    """
    storage = defaultdict(ErrorState)

    def set_error(self, topic: str, error: ErrorType) -> None:
        if error is ErrorType.read:
            self.storage[topic].read = True
        else:
            self.storage[topic].write = True

    def remove_error(self, topic: str, error: ErrorType) -> None:
        if error is ErrorType.read:
            self.storage[topic].read = False
        else:
            self.storage[topic].write = False

    def get_state(self, topic: str) -> str:
        result = ''
        if self.storage[topic].read:
            result += 'r'
        if self.storage[topic].write:
            result += 'w'
        return result


class TopicManager:
    """
    Класс, который отвечает за трансляцию взаимодействия между MQTT и протоколом MIot
    """
    def __init__(self, meta_topics: dict, mqtt_client: aiomqtt.Client, device_name: str):
        self.device_name = device_name
        self.mqtt_client = mqtt_client
        self.meta_topics = meta_topics
        self.err_state = ErrorManager()

    async def publish_meta(self) -> None:
        # Публикуем метаинформацию об устройстве из файла в MQTT в соответствии с конвенцией wirenboard MQTT
        # json файл содержит готовые json для публикации в contols/../meta
        for k,v in self.meta_topics.items():
            meta_data = v.copy()
            if k == 'meta':
                # Публикация высокоуровневой метинформации об устройстве
                topic = self.create_topic_name('')
                # Публикуем английское написание
                await self.mqtt_client.publish(topic + f'/meta/name', payload=meta_data['title']['en'], retain=True)
                await self.mqtt_client.publish(topic + f'/meta/driver', payload=meta_data['driver'], retain=True)
            else:
                # Публикация метаинформации по конкретным ручкам устройства
                topic = self.create_topic_name(k)
                for f in ['type', 'order', 'readonly', 'min', 'max']:
                    if f == 'readonly':
                        # true, false превращаем в 0 или 1 для публикации в топик /meta/readonly
                        # потому что в json который в /meta там должно быть true/false и это более удобно для чтения
                        value = int(meta_data.get(f, False))
                    else:
                        # для остальных значений трансморфмация не требуется
                        value = meta_data.get(f)
                    if value is not None:
                        # если флаг есть в json метафайле, то публикуем его в топик
                        await self.mqtt_client.publish(topic + f'/meta/{f}', payload=value, retain=True)
            # публикуем готовый json в топики
            await self.mqtt_client.publish(topic + '/meta', payload=json.dumps(meta_data), retain=True)
            # поскольку сначала идет публикация мета инфы, а потом уже идет заполнение полезными значениями
            # поэтому помечаем ручки ошибкой для чтения
            await self.mqtt_client.publish(topic + '/meta/error', payload=self.err_state.get_state(topic), retain=True)

    async def publish_error_state(self, all_error: ErrorType or None = None) -> None:
        """
        Массовая публикация топиков для чтения ошибкой
        полезно при старте сервиса, его завершение или при ошибке получения информации с устройства
        """
        for t in self.get_control_read_topics():
            if all_error is not None:
                self.err_state.set_error(t, all_error)
            await self.mqtt_client.publish(t + '/meta/error', payload=self.err_state.get_state(t), retain=True)

    def create_topic_name(self, name: str) -> str:
        if name == '':
            return f'/devices/{self.device_name}'
        return f'/devices/{self.device_name}/controls/{name}'

    async def subscribe_topics(self) -> List[str]:
        topics = self.get_control_write_topics()
        tasks = [self.mqtt_client.subscribe(t) for t in topics]
        res = await asyncio.gather(*tasks)
        # срезаем /on постфикс
        return [x[:-3] for x, _ in takewhile(lambda x: x[1][0].is_failure, zip(topics, res))]

    def get_control_write_topics(self) -> List[str]:
        topics = []
        for k, v in self.meta_topics.items():
            if v.get('readonly', False):
                # пропускаем ручки которые только для чтения
                continue
            if k == 'meta':
                continue
            else:
                topics.append(self.create_topic_name(k) + '/on')
        return topics

    def get_control_read_topics(self) -> List[str]:
        topics = []
        for k, v in self.meta_topics.items():
            if k == 'meta':
                continue
            else:
                topics.append(self.create_topic_name(k))
        return topics

    async def publish_status(self, status):
        for k, v in status.data.items():
            v = self.transform_publish_value(k, v)

            topic = self.create_topic_name(k)
            self.err_state.remove_error(topic, ErrorType.read)
            await self.mqtt_client.publish(
                topic + '/meta/error',
                payload=self.err_state.get_state(topic),
                retain=True
            )
            await self.mqtt_client.publish(topic, payload=v, retain=True)

    def transform_publish_value(self, control_name: str, value: Any) -> Any:
        if control_name in ['use_time', 'power_time']:
            value = value // 3600
        if control_name == 'water_level':
            value = value // 1.27
        if control_name in self.meta_topics:
            if isinstance(value, bool):
                # в топик надо отправлять 0 или 1 вместо true/false
                value = int(value)
        return value

    def parse_message(self, message: Message) -> (str, Any):
        # так как по Wirenboard MQTT топики называются /devices/<devname>/controls/<controlname>/
        control = message.topic.value.split('/')[-2]
        data = json.loads(message.payload)
        return control, data


class EventCycle:
    """
    Класс, который обеспечивает цикл обработки входящий собыйти или генерирует их сам
    """
    action_mapping = None
    dev: AirHumidifierMiot = None
    publish_states_task = None
    consume_message_task = None

    def __init__(self, client: aiomqtt.Client, interval: int, meta_topics: dict, dev_param: DeviceParams):
        self.dev_param = dev_param
        self.interval = interval
        self.client = client
        self.tm = TopicManager(meta_topics, client, dev_param.device_name)
        self.pending = set()

    async def configure_client(self):
        """
        Подготовка mqtt окружения перед началом работы
        :return:
        """
        await self.tm.publish_meta()
        err_topics = await self.tm.subscribe_topics()
        # если подписка на топики произошла с ошибкой надо соответствующим образом отметить их состояние
        if err_topics:
            logging.error('Failed subscribe topics: %s', err_topics)
            for t in err_topics:
                self.tm.err_state.remove_error(t, ErrorType.write)
                await self.client.publish(
                    t + '/meta/error',
                    payload=self.tm.err_state.get_state(t),
                    retain=True
                )
            raise Exception('subscribe error')

    def create_action_mapping(self):
        """
        Таблица трансляции сигналов из mqtt топиков в команды для устройства
        для каждого топика имеем соответсвующий префикс, функцию трансформации значения из топка и метод, который
        надо дернуть на устройстве чтобы действие применилось
        :return:
        """
        value_mapping = {
            # 'controlname': (transform value from MQTT as arg of dev api method)
            'power': (lambda x: bool(x), lambda x: self.dev.on() if x else self.dev.off()),
            'mode': (lambda x: OperationMode(x), lambda x: self.dev.set_mode(x)),
            'target_humidity': (lambda x: x, self.dev.set_target_humidity),
            'speed_level': (lambda x: x, self.dev.set_speed),
            'dry': (lambda x: bool(x), self.dev.set_dry),
            'buzzer': (lambda x: bool(x), self.dev.set_buzzer),
            'led_brightness': (lambda x: LedBrightness(x), self.dev.set_led_brightness),
            'child_lock': (lambda x: bool(x), self.dev.set_child_lock),
            'clean_mode': (lambda x: bool(x), self.dev.set_clean_mode)
        }
        return value_mapping

    def create_dev(self):
        """Подключение к устройству по протоколу MIot"""
        if self.publish_states_task is not None:
            self.publish_states_task.cancel()
        logging.info("Recreate device: %s", self.dev_param.device_name)
        # AirHumidifierMiot
        self.dev = DeviceFactory.create(
            self.dev_param.ip,
            self.dev_param.token,
            model=self.dev_param.type
        )
        self.action_mapping = self.create_action_mapping()

    async def publish_states(self) -> bool:
        """
        Опрос устройства с последующей публикацией полученных значений в mqtt
        """
        await asyncio.sleep(self.interval)
        need_new_dev = False
        try:
            last_status = self.dev.status()
        except Exception as e:
            logging.exception(e)
            need_new_dev = True
            await self.tm.publish_error_state(ErrorState.read)
        else:
            await self.tm.publish_status(last_status)
        logging.debug("Publish states complete")
        return need_new_dev

    def create_publish_states_task(self):
        return asyncio.create_task(self.publish_states(), name='publish_states')

    def create_consume_message_task(self):
        return asyncio.create_task(anext(self.client.messages), name='consume_message')

    async def run(self):
        """
        Главный цикл работы приложения, раз в interval ждем одно из двух событий: или приходит команда из mqtt
        или опрашиваем устройство и публикуем его состояние
        """
        new_dev = True
        self.consume_message_task = self.create_consume_message_task()
        while True:
            # Если требуется, то переподключаемся к устройству
            if new_dev:
                self.create_dev()
                self.publish_states_task = self.create_publish_states_task()

            # ждем одно из 2 событий в зависимости от того что произойдет раньше
            done, pending = await asyncio.wait(
                [self.publish_states_task, self.consume_message_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            # распаковываем нашу функцию из списка
            done = done.pop()
            # Определяем какое же это все таки событие
            if done is self.publish_states_task:
                new_dev = done.result()
                self.publish_states_task = self.create_publish_states_task()
            else:
                # Получили сообщение
                message = done.result()
                logging.debug('Received message: %s' % message.payload)
                # Распарсили его
                control, data = self.tm.parse_message(message)
                logging.debug('Received topic: %s' % control)
                # Определяем ручку на устройстве согласно сообщения
                transformer, executor = self.action_mapping[control]
                topic = self.tm.create_topic_name(control)
                try:
                    # Дергаем ручку
                    executor(transformer(data))
                except Exception as e:
                    # не смогли дернуть ручку
                    logging.exception(e)
                    new_dev = True
                    self.tm.err_state.set_error(topic, ErrorType.read)
                else:
                    # смогли дернуть ручку
                    self.tm.err_state.remove_error(topic, ErrorType.read)
                await self.client.publish(
                    topic + '/meta/error',
                    payload=self.tm.err_state.get_state(topic),
                    retain=True
                )
                # заново начинаем ждать сообщение из топиков
                self.consume_message_task = self.create_consume_message_task()


async def device_thread(mqtt_address: str, meta_topics: dict, dev_param: DeviceParams) -> None:
    logging.info('Start service')
    client = aiomqtt.Client(mqtt_address)
    interval = 5  # Seconds
    while True:
        try:
            logging.info('Connect to mqtt')
            async with client:
                logging.info('Connect to mqtt -> success')
                e = EventCycle(client, interval, meta_topics, dev_param)
                logging.info('Configure mqtt')
                await e.configure_client()
                logging.info('Configure mqtt -> success')
                logging.info('Run event cycle...')
                await e.run()

        except aiomqtt.MqttError:
            # for t in pending:
            #     t.cancel()
            logging.error(f"Connection lost; Reconnecting in {interval} seconds ...")
            await asyncio.sleep(interval)


with open("configs/meta_topics.json", "r") as f:
    meta_topics = json.load(f)

with open("configs/devices.json", "r") as f:
    devices = json.load(f)

mqtt_address = os.getenv('MQTT_ADDR') or 'wb.lan'
if os.getenv("DEBUG"):
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

async def main():
    tasks = [asyncio.Task(device_thread(mqtt_address, meta_topics, DeviceParams(**d))) for d in devices]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

asyncio.run(main())
