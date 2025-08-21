import os
from urllib.parse import urlparse, urlunparse, urljoin, urlsplit

from src.managers.sys_setting import SysSetting

class PathManager():
    def __init__(self):
        pass

    @classmethod
    def JoinPath(cls, filePath, fileName):
        return os.path.join(filePath, fileName)
    
    @classmethod
    def GetRltPath(cls, fileName):
        '''获取文件的相对路径 RelativePath'''
        if os.path.isabs(fileName):
            workPath = SysSetting.GetWorkPath()
            return fileName.replace(workPath, "")
        return fileName
    
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
    def GetRltDir(cls, fileName):
        return fileName.rsplit(os.sep, 1)[0]

    @classmethod
    def MakeDirsByPath(cls, filePath: str):
        # 判断保存文件夹 路径是否存在。无则创建
        if not os.path.exists(filePath):
            os.makedirs(filePath)

    @classmethod
    def MakeDirsByFile(cls, absFile: str):
        absPath = absFile.rsplit(os.sep, 1)[0]
        # 判断保存文件夹 路径是否存在。无则创建
        if not os.path.exists(absPath):
            os.makedirs(absPath)
    
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
        print(f"FileManager.GetPathFromURI filePath:{filePath} fileName:{fileName} absName:{absName}")

        # # 2. 判断保存文件夹 路径是否存在。无则创建
        # if not os.path.exists(savePath):
        #     os.makedirs(savePath)

        # 判断文件是否存在，无则创建
        # return savePath, saveFile
        return filePath, fileName, absName