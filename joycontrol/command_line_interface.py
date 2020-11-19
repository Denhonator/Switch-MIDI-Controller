import inspect
import logging
import shlex
import mido
import asyncio
import time

from aioconsole import ainput

from joycontrol.controller_state import button_push, ControllerState
from joycontrol.transport import NotConnectedError

logger = logging.getLogger(__name__)

midi_to_key = {38: "hold a&&hold y",
               48: "hold x",
               42: "hold l&&hold zl",
               36: "hold r&&hold zr",
               51: "stick l h 3200&&stick r h 3200",
               55: "stick l h 848&&stick r h 848",
               45: "stick l up&&stick r up",
               41: "stick l down&&stick r down",
               6: "hold b&&stick l center",
               5: "release b&&stick l center",
               7: "stick l center"}

arlBuffer = {}

def _print_doc(string):
    """
    Attempts to remove common white space at the start of the lines in a doc string
    to unify the output of doc strings with different indention levels.

    Keeps whitespace lines intact.

    :param fun: function to print the doc string of
    """
    lines = string.split('\n')
    if lines:
        prefix_i = 0
        for i, line_0 in enumerate(lines):
            # find non empty start lines
            if line_0.strip():
                # traverse line and stop if character mismatch with other non empty lines
                for prefix_i, c in enumerate(line_0):
                    if not c.isspace():
                        break
                    if any(lines[j].strip() and (prefix_i >= len(lines[j]) or c != lines[j][prefix_i])
                           for j in range(i+1, len(lines))):
                        break
                break

        for line in lines:
            print(line[prefix_i:] if line.strip() else line)


class CLI:
    def __init__(self):
        self.commands = {}

    def add_command(self, name, command):
        if name in self.commands:
            raise ValueError(f'Command {name} already registered.')
        self.commands[name] = command

    async def cmd_help(self):
        print('Commands:')
        for name, fun in inspect.getmembers(self):
            if name.startswith('cmd_') and fun.__doc__:
                _print_doc(fun.__doc__)

        for name, fun in self.commands.items():
            if fun.__doc__:
                _print_doc(fun.__doc__)

        print('Commands can be chained using "&&"')
        print('Type "exit" to close.')

    async def run(self):
        count = pg.get_count()
        for i in range(count):
            print(pg.get_device_info(i))
        inp = pg.Input(3)

        while True:
            user_input = await ainput(prompt='cmd >> ')
            if not user_input:
                continue

            for command in user_input.split('&&'):
                cmd, *args = shlex.split(command)

                if cmd == 'exit':
                    return

                if hasattr(self, f'cmd_{cmd}'):
                    try:
                        result = await getattr(self, f'cmd_{cmd}')(*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                elif cmd in self.commands:
                    try:
                        result = await self.commands[cmd](*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                else:
                    print('command', cmd, 'not found, call help for help.')

    @staticmethod
    def deprecated(message):
        async def dep_printer(*args, **kwargs):
            print(message)

        return dep_printer


class ControllerCLI(CLI):
    def __init__(self, controller_state: ControllerState):
        super().__init__()
        self.controller_state = controller_state

    async def cmd_help(self):
        print('Button commands:')
        print(', '.join(self.controller_state.button_state.get_available_buttons()))
        print()
        await super().cmd_help()

    @staticmethod
    def _set_stick(stick, direction, value):
        if direction == 'center':
            stick.set_center()
        elif direction == 'up':
            stick.set_up()
        elif direction == 'down':
            stick.set_down()
        elif direction == 'left':
            stick.set_left()
        elif direction == 'right':
            stick.set_right()
        elif direction in ('h', 'horizontal'):
            if value is None:
                raise ValueError(f'Missing value')
            try:
                val = int(value)
            except ValueError:
                raise ValueError(f'Unexpected stick value "{value}"')
            stick.set_h(val)
        elif direction in ('v', 'vertical'):
            if value is None:
                raise ValueError(f'Missing value')
            try:
                val = int(value)
            except ValueError:
                raise ValueError(f'Unexpected stick value "{value}"')
            stick.set_v(val)
        else:
            raise ValueError(f'Unexpected argument "{direction}"')

        return f'{stick.__class__.__name__} was set to ({stick.get_h()}, {stick.get_v()}).'

    async def cmd_stick(self, side, direction, value=None):
        """
        stick - Command to set stick positions.
        :param side: 'l', 'left' for left control stick; 'r', 'right' for right control stick
        :param direction: 'center', 'up', 'down', 'left', 'right';
                          'h', 'horizontal' or 'v', 'vertical' to set the value directly to the "value" argument
        :param value: horizontal or vertical value
        """
        if side in ('l', 'left'):
            stick = self.controller_state.l_stick_state
            return ControllerCLI._set_stick(stick, direction, value)
        elif side in ('r', 'right'):
            stick = self.controller_state.r_stick_state
            return ControllerCLI._set_stick(stick, direction, value)
        else:
            raise ValueError('Value of side must be "l", "left" or "r", "right"')

    async def run(self):
        inputs = mido.get_input_names()
        portname = ""
        for i in inputs:
            if "drum" in i.lower():
                portname = i
                break

        inport = mido.open_input(portname)
        timer = time.perf_counter()
        jumpstate = False
        menumode = True
        menuCounter = 0

        while True:
            dtime = time.perf_counter()-timer
            timer = time.perf_counter()
            msg = inport.poll()
            for k in arlBuffer.keys():
                arlBuffer[k] -= dtime
            user_input = ""
            if msg:
                if msg.type=="note_on":
                    msg.note = 42 if msg.note == 46 or (msg.note==38 and 38 in arlBuffer.keys()) else msg.note
                    user_input = midi_to_key.get(msg.note, "")
                    arlBuffer[msg.note] = 0.04 if not "stick" in user_input else 0.3
                    if msg.note==48:
                        menuCounter += 1
                        if menuCounter>10:
                            menumode = not menumode
                            menuCounter = 0
                            print("menumode "+str(menumode))
                    else:
                        menuCounter = 0
                elif msg.type=="control_change":
                    if msg.value > 64 and not jumpstate:
                        user_input = midi_to_key.get(6, "")
                        jumpstate = True
                    elif msg.value <= 64 and jumpstate:
                        user_input = midi_to_key.get(5, "")
                        jumpstate = False
            else:
                for k in arlBuffer.keys():
                    if arlBuffer[k]<=0:
                        user_input = midi_to_key.get(k, "").replace("hold", "release")
                        for s in midi_to_key.get(k, "").split("&&"):
                            if "stick" in s:
                                user_input = user_input.replace(s.split(" ", 2)[-1], "center")
                        del arlBuffer[k]
                        break
            if not user_input:
                await asyncio.sleep(0.005)
                continue
            if menumode:
                user_input = user_input.split("&&")[0]
            print(user_input)
            buttons_to_push = []

            for command in user_input.split('&&'):
                cmd, *args = shlex.split(command)

                if cmd == 'exit':
                    return

                available_buttons = self.controller_state.button_state.get_available_buttons()

                if hasattr(self, f'cmd_{cmd}'):
                    try:
                        result = await getattr(self, f'cmd_{cmd}')(*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                elif cmd in self.commands:
                    try:
                        result = await self.commands[cmd](*args)
                        if result:
                            print(result)
                    except Exception as e:
                        print(e)
                elif cmd in available_buttons:
                    buttons_to_push.append(cmd)
                else:
                    print('command', cmd, 'not found, call help for help.')

            if buttons_to_push:
                print("button push")
                await button_push(self.controller_state, *buttons_to_push)
            #else:
            #    try:
            #        await self.controller_state.send()
            #    except NotConnectedError:
            #        logger.info('Connection was lost.')
            #        return
