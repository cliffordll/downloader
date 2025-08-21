from threading import Thread
import requests
from requests.exceptions import (ConnectionError, ConnectTimeout, ReadTimeout, SSLError, TooManyRedirects)
from src.managers.sys_setting import SysSetting
from queue import Queue
from functools import partial

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

class ThreadPoolManager:
    _instance = None
    _lock = threading.Lock()

    # 单例模式
    def __new__(cls, maxWorkers=3):
        if cls._instance is None:
            with cls._lock:
                # 再次检查,因为可能有多个线程同时通过了第一次检查
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, maxWorkers=3):
        self.executor = ThreadPoolExecutor(max_workers=maxWorkers)
        self.maxWorkers = maxWorkers
 
    def submit(self, func, *args, **kwargs):
        """提交一个任务到线程池执行"""
        future = self.executor.submit(func, *args, **kwargs)
        return future
 
    def map(self, func, iterable):
        """将一个函数应用于可迭代对象的每个元素，返回一个迭代器"""
        return self.executor.map(func, iterable)
 
    def shutdown(self, wait=True):
        """关闭线程池"""
        self.executor.shutdown(wait=wait)

class Downloader(object):
    isStop = True
    threadLock = threading.Lock()
    threadQueue = Queue()

    maxWorkers = SysSetting().GetMaxWorkers()
    threadPool = ThreadPoolManager(maxWorkers=maxWorkers)

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
    def _DownLoadFile(cls, baseUrl: str, fileName: str):
        print(f'Downloader.Downloading {baseUrl}')
        try:
            flag, content = cls.DownloadContent(baseUrl)
            if flag:
                with open(fileName, 'wb') as f:
                    f.write(content)
                print(f'Downloader.Downloaded {fileName}')
                return True, fileName
            else:
                print(f'Downloader._DownLoadFile Error: {content}')
        except requests.RequestException as ex:
            print(f'Downloader._DownLoadFile Exception: {ex}')
        return False, fileName


    ###################################
    ### 添加TS下载任务
    ###################################
    @classmethod
    def DownloadTSFile(cls, absUri:str, absFile: str, callback, item):
        try:
            # print(f"Downloader.DownloadFile absUri:{absUri}")
            # print(f"Downloader.DownloadFile absFile:{absFile}")
            cls._AddTask((absUri, absFile, callback, item))

            # 如果下载主线程未开启，则开启主线程
            if cls.isStop:
                cls._StartMasterThread()
        except Exception as ex:
            print(f"Downloader.DownloadFile except:{str(ex)}")
        return
    
    ###################################
    ### 下载任务管理部分
    ###################################
    @classmethod
    def _AddTask(cls, args):
        # print(f"Downloader.DownloadFile absUri:{absUri}")
        # print(f"Downloader.DownloadFile absFile:{absFile}")
        with cls.threadLock:
            cls.threadQueue.put(args)
            print(f"Downloader.DownloadFile addTask total:{cls.threadQueue.qsize()}")
    
    @classmethod
    def _GetTasks(cls, count: int=2):
        tasks = []
        with cls.threadLock:
            while not cls.threadQueue.empty() and len(tasks) < count:
                task = cls.threadQueue.get()
                tasks.append(task)
            else:
                print(f"Downloader._MasterThread count:{count} qsize:{cls.threadQueue.qsize()}")
        return tasks    

    @classmethod
    def _TaskCallback(cls, callback, item, future):
        flag, fileName = future.result()
        callback(flag, fileName, item)
    
    @classmethod
    def _MasterThreadRun(cls):
        '''有任务执行则执行，无任务执行则退出'''
        count = 0
        while True:
            if cls.isStop:
                print("Downloader.Master Thread Stop!!!!!!!!!!")
                break
            # print(cls.threadPool.maxWorkers)

            tasks = cls._GetTasks(cls.threadPool.maxWorkers)
            if len(tasks) > 0:
                # 提交任务到线程池
                futures = []
                for task in tasks:
                    # (absUri, absFile, callback, item)
                    future = cls.threadPool.submit(cls._DownLoadFile, task[0], task[1])

                    # task[2] 页面回调
                    # task[3] 多列树项
                    callback_with_args = partial(cls._TaskCallback, task[2], task[3])
                    future.add_done_callback(callback_with_args)
                    futures.append(future)
                
                # 等待所有任务完成并获取结果
                for future in as_completed(futures):
                    future.result()
            else:
                cls.isStop = True
                
            count+=1
            time.sleep(1)

    @classmethod
    def _StartMasterThread(cls):
        cls.isStop = False
        # 创建并启动子线程
        thread = threading.Thread(target=cls._MasterThreadRun)
        thread.start()