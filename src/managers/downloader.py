from threading import Thread
import requests

from src.managers.sys_setting import SysSetting

class Downloader(object):
    def __init__(self):
        pass

    @classmethod
    def DownloadContent(cls, baseUrl):
        try:
            response = requests.get(baseUrl)
            response.raise_for_status()

            # with open(ts_file, 'wb') as f:
            #     f.write(response.content)
            # print(f'Downloaded {ts_file}')
            return response.content
        except requests.RequestException as e:
            print(f'Error downloading {baseUrl}: {e}')

    @classmethod
    def _DownLoadFile(cls, baseUrl: str, fileName: str, callback):
        try:
            timeout = SysSetting.GetTimeout()

            # print(absFile)
            response = requests.get(baseUrl, timeout=timeout)
            # response.raise_for_status()
            if response.status_code == 200:
                with open(fileName, 'wb') as f:
                    f.write(response.content)
                print(f'Downloader.Downloaded {fileName}')

                callback(True, fileName)
            else:
                print(f'Downloader._DownLoadFile Error: {response.status_code}')
                response.content
                callback(False, fileName)
            
        except requests.RequestException as ex:
            print(f'Downloader._DownLoadFile Exception: {ex}')
            callback(False, "文件下载超时异常")
        return

    
    @classmethod
    def DownloadTSFile(cls, absUri:str, absFile: str, callback):
        try:
            print(f"Downloader.DownloadFile absUri:{absUri}")
            print(f"Downloader.DownloadFile absFile:{absFile}")

            # 使用线程控制下载
            t1 = Thread(target=cls._DownLoadFile, args=(absUri, absFile, callback))
            # 如果有参数
            # t2 = threading.Thread(target=consumer_task_queue, args=(taskqueue, db, ds, tokenizer, evaltool))
            # def consumer_task_queue(taskqueue, db, ds, tokenizer, evaltool):
            # 启动
            t1.start()
            print(f"Downloader.DownloadFile thread start......")
        except Exception as ex:
            print(f"Downloader.DownloadFile except:{str(ex)}")
        return