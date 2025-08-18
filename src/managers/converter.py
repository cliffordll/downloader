
from multiprocessing import Process
import time

import ffmpeg

from threading import Thread
import subprocess
 
class Converter():
    def __init__(self):
        pass
        
    @classmethod
    def _ConvertTSFile(cls, playlist: str, outputFile: str="output.mp4", callback=None):
        # ffmpeg -f concat -safe 0 -i playlist.txt -c copy output.mp4
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', playlist,
            '-c', 'copy',
            outputFile,
            '-progress', 'pipe:1'     # 输出进度信息
        ]
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            # print(f"Converter.ConvertTSFile Progress: {line.strip()}")
            if 'out_time_ms' in line:
                time_ms = int(line.split('=')[1])
                print(f"Converter.ConvertTSFile Progress: {time_ms/1000000:.2f} seconds")
        print(f"Converter.ConvertTSFile Progress: END")
        process.wait()

    @classmethod
    def ConvertTSFile(cls, playlist: str, outputFile: str, callback=None):
        try:
            print(f"Converter.ConvertTSFile playlist:{playlist}")
            print(f"Converter.ConvertTSFile outputFile:{outputFile}")

            # 使用线程控制下载
            t1 = Thread(target=cls._ConvertTSFile, args=(playlist, outputFile, callback))
            # 如果有参数
            # t2 = threading.Thread(target=consumer_task_queue, args=(taskqueue, db, ds, tokenizer, evaltool))
            # def consumer_task_queue(taskqueue, db, ds, tokenizer, evaltool):
            # 启动
            t1.start()
            print(f"Converter.ConvertTSFile thread start......")
        except Exception as ex:
            print(f"Converter.ConvertTSFile except:{str(ex)}")
        return