from threading import Thread
import requests
from requests.exceptions import (ConnectionError, ConnectTimeout, ReadTimeout, SSLError, TooManyRedirects)
from src.managers.sys_setting import SysSetting

class Downloader(object):
    def __init__(self):
        pass

    @classmethod
    def DownloadContent(cls, baseUrl):
        try:
            timeout = SysSetting.GetTimeout()
            response = requests.get(baseUrl, timeout=timeout)
            # response.raise_for_status()
            if response.status_code == 200:
                return True, response.content
            else:
                return False, response.content
        except ConnectionError as ex:
            print(f'Downloader.ConnectionError Exception: {ex}')
            return False, "无法建立连接，可能是网络问题或服务器不可用"
        except ConnectTimeout as ex:
            print(f'Downloader.ConnectTimeout Exception: {ex}')
            return False, "连接服务器超时"
        except ReadTimeout as ex:
            print(f'Downloader.ReadTimeout Exception: {ex}')
            return False, "服务器响应超时"
        except SSLError as ex:
            print(f'Downloader.SSLError Exception: {ex}')
            return False, "SSL证书验证失败"
            # 可以选择忽略证书验证(不推荐用于生产环境)
            # response = requests.get(url, verify=False)   
        except TooManyRedirects as ex:
            print(f'Downloader.TooManyRedirects Exception: {ex}')
            return False, "重定向次数过多"
        except requests.exceptions.HTTPError as ex:
            print(f'Downloader.HTTPError Exception: {ex}')
            return False, f"HTTP错误: {str(ex)}"
        except Exception as ex:
            print(f'Downloader.Exception Exception: {ex}')
            return False, f"其他错误: {str(ex)}"
        # return False, "Download Error"

    @classmethod
    def _DownLoadFile(cls, baseUrl: str, fileName: str, callback, item):
        try:
            flag, content = cls.DownloadContent(baseUrl)
            if flag:
                with open(fileName, 'wb') as f:
                    f.write(content)
                print(f'Downloader.Downloaded {fileName}')
                callback(True, fileName, item)
            else:
                print(f'Downloader._DownLoadFile Error: {content}')
                # response.content
                callback(False, fileName, item)

            # timeout = SysSetting.GetTimeout()
            # # print(absFile)
            # response = requests.get(baseUrl, timeout=timeout)
            # # response.raise_for_status()
            # if response.status_code == 200:
            #     with open(fileName, 'wb') as f:
            #         f.write(response.content)
            #     print(f'Downloader.Downloaded {fileName}')

            #     callback(True, fileName)
            # else:
            #     print(f'Downloader._DownLoadFile Error: {response.status_code}')
            #     # response.content
            #     callback(False, fileName)
        except requests.RequestException as ex:
            print(f'Downloader._DownLoadFile Exception: {ex}')
            callback(False, "文件下载超时异常")
        return

    
    @classmethod
    def DownloadTSFile(cls, absUri:str, absFile: str, callback, item):
        try:
            print(f"Downloader.DownloadFile absUri:{absUri}")
            print(f"Downloader.DownloadFile absFile:{absFile}")

            # 使用线程控制下载
            t1 = Thread(target=cls._DownLoadFile, args=(absUri, absFile, callback, item))
            # 如果有参数
            # t2 = threading.Thread(target=consumer_task_queue, args=(taskqueue, db, ds, tokenizer, evaltool))
            # def consumer_task_queue(taskqueue, db, ds, tokenizer, evaltool):
            # 启动
            t1.start()
            print(f"Downloader.DownloadFile thread start......")
        except Exception as ex:
            print(f"Downloader.DownloadFile except:{str(ex)}")
        return