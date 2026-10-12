import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse, urlsplit

from src.core.sys_setting import SysSetting

class PathManager():

    @classmethod
    def JoinPath(cls, filePath, fileName):
        return os.path.join(filePath, fileName)
    
    
    @classmethod
    def GetAbsPath(cls, fileName: str):
        if not os.path.isabs(fileName):
            workPath = SysSetting.GetWorkPath()
            absFile = os.path.join(workPath, fileName)
            # print(absFile)
            return absFile
        return fileName

    @classmethod
    def GetAbsDir(cls, fileName):
        absFile = cls.GetAbsPath(fileName)
        return absFile.rsplit(os.sep, 1)[0]



    
    @classmethod
    def IsExists(cls, absFile:str):
        '''判断文件或文件夹是否存在'''
        if not os.path.exists(absFile):
            return False
        return True

    @classmethod
    def GetPathFromURI(cls, baseUri: str):
        parsedUrl = urlparse(baseUri)
        # print(parsedUrl.scheme)    # 输出协议：https
        # print(parsedUrl.netloc)    # 输出域名：www.example.com
        # print(parsedUrl.path)      # 输出路径：/path
        # print(parsedUrl.params)    # 
        # print(parsedUrl.query)     # 输出查询参数：param1=value1&param2=value2
        # print(parsedUrl.fragment)  # 片段标识符: 

        saveDirs = parsedUrl.path.split('/')
        # print(saveDirs)
        # print(saveDirs[1:-1])
        filePath = os.path.join("", *saveDirs[1:-1])
        fileName = saveDirs[-1]
        absName = os.path.join("", *saveDirs[1:])

        # # 2. 判断保存文件夹 路径是否存在。无则创建
        # if not os.path.exists(savePath):
        #     os.makedirs(savePath)

        # 判断文件是否存在，无则创建
        # return savePath, saveFile
        return filePath, fileName, absName

    @classmethod
    def GetFileNameFromURI(cls, url):
        name = unquote(urlsplit(url.strip()).path.rsplit('/', 1)[-1])
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
        stem = Path(name).stem if name else ''
        if not stem or stem.casefold() in ('index', 'playlist', 'master'):
            stem = 'video_' + datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            return cls.ValidateFileName(stem[:100] + '.mp4')
        except ValueError:
            return 'video_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.mp4'


    @classmethod
    def ValidateFileName(cls, value):
        name = unicodedata.normalize('NFC', value.strip())
        if (not name or name in ('.', '..') or name.endswith(('.', ' '))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)):
            raise ValueError('文件名不能为空，也不能包含路径或特殊字符。')
        if not name.lower().endswith('.mp4'):
            name += '.mp4'
        stem = name[:-4]
        if (not stem.strip(' .') or stem.endswith((' ', '.'))
                or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', stem.split('.')[0])):
            raise ValueError('请使用有效的视频文件名。')
        if len(name.encode('utf-8')) > 180:
            raise ValueError('文件名过长，请缩短后重试。')
        return name


    @classmethod
    def GetPathKey(cls, path):
        # Windows/macOS 常见文件系统不区分大小写；同时统一 Unicode 表示。
        return unicodedata.normalize('NFC', str(Path(path).resolve())).casefold()


    @classmethod
    def PublishFile(cls, temporary, target):
        """同卷发布完整文件；目标已存在时原子失败，不能覆盖外部文件。"""
        os.link(temporary, target)
        Path(temporary).unlink()
