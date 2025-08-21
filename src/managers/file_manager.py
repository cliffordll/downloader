import os
from urllib.parse import urlparse, urlunparse, urljoin, urlsplit

from src.schemas.file_base import TreeData, TreeItem, FileItem

from src.managers.path_manager import PathManager
from src.managers.m3m8_parser import M3U8Parser
from src.managers.sys_setting import SysSetting

class FileManager():
    def __init__(self):
        pass

    @classmethod
    def GetFileItem(cls, absFile: str, absUri: str='-'):
        '''组装FileItem'''
        rltPath = PathManager.GetRltPath(absFile)          # 截取相对路径，不然太长了

        if os.path.exists(absFile):
            fileSize    = os.path.getsize(absFile)
            timeModify  = os.path.getmtime(absFile) # 返回float类型时间戳
            return True, FileItem(fileName=rltPath, fileSize=fileSize, modifyAt=timeModify, absUri=absUri)
        return False, FileItem(fileName=rltPath, fileSize="-", modifyAt="-", absUri=absUri)

    @classmethod
    def GetFileInfos(cls):
        workPath = SysSetting.GetWorkPath()
        treeData = TreeData()

        for root, dirs, files in os.walk(workPath):
            for file in files:
                absSeed = os.path.join(root, file)

                if file.endswith("seed"):
                    treeItem = TreeItem()
                    flag, fileItem = cls.GetFileItem(absSeed)
                    treeItem.parent = fileItem
                    
                    # basePath, baseUri, content = cls.ParseSeedFile(absPath)
                    # for ts in cls._CheckM3U8File(basePath, baseUri, content):
                    for ts in cls.GetSegmentList(absSeed):
                        tsAbs = PathManager.JoinPath(root, ts.name)
                        flag, fileItme = cls.GetFileItem(tsAbs, ts.absUri)
                        if flag:    # 统计下载个数
                            treeItem.download += 1
                        treeItem.childs.append(fileItme)

                    treeData.items.append(treeItem)
                else:
                    pass
        return treeData
    
    # @classmethod
    # def GetUriByIdx(cls, absSeed: str, index: int):
    #     # 读取 m3u8 内容获取下载地址
    #     try:
    #         basePath, baseUri, content = cls.ParseSeedFile(absSeed)
    #         tsList = cls._CheckM3U8File(basePath, baseUri, content)
    #         if len(tsList) > 0:
    #             tsName = tsList[index].name
    #             absUri = tsList[index].absUri
    #             # print(f"FileManager.GetUriByIdx index:{index} basePath:{basePath}")
    #             # print(f"FileManager.GetUriByIdx index:{index} m3u8Url:{baseUri}")
    #             # print(f"FileManager.GetUriByIdx index:{index} asbUri:{absUri}")
    #             return tsName, absUri
    #     except Exception as ex:
    #         print(f"FileManager.GetUriByIdx except {str(ex)}")  
    #     return "", ""
    @classmethod
    def GetSegmentList(cls, absSeed: str):
        # 读取 m3u8 内容获取下载地址
        tsList = []
        try:
            basePath, baseUri, content = cls._ParseSeedFile(absSeed)
            return cls._CheckM3U8File(basePath, baseUri, content)
        except Exception as ex:
            print(f"FileManager.GetSegmentList except {str(ex)}")  
        return tsList

    @classmethod
    def CreatePlaylist(cls, absSeed: str, playDir: str, playlist: str):
        try:
            # 读取路径
            tsNames = ""
            # basePath, baseUri, content = cls.ParseSeedFile(absSeed)
            # tsList = cls._CheckM3U8File(basePath, baseUri, content)
            tsList = cls.GetSegmentList(absSeed)
            for idx, ts in enumerate(tsList):
                if idx > 0:
                    tsNames += "\n"
                # 因为要用 ffmpeg 进程，所以指定了绝对路径
                tsName = SysSetting.GetPath(playDir, ts.name)
                tsNames += f"file '{tsName}'"

            # with open(playFile, 'wb') as f:     # 不存在则创建
            #     f.write(tsNames.encode())       # 可写入初始内容
            with open(playlist, 'w') as f:     # 不存在则创建
                f.write(tsNames)       # 可写入初始内容
        except Exception as ex:
            print(f"FileManager.GetPlaylist except {str(ex)}")

    '''
    basePath: 下载路径
    baseUri: 下载连接地址
    content: 下载内容
    '''
    @classmethod
    def _CheckM3U8File(cls, basePath: str, baseUri, content: str):
        tsList = []
        try:
            parser = M3U8Parser(content=content, base_path=basePath, m3u8_uri=baseUri)
            # print("FileManager.CheckSeedFile")
            # print("FileManager.CheckSeedFile")
            tsList = parser.parse_media()
        except Exception as ex:
            print(f"FileManager._CheckM3U8File except:{str(ex)}")
        return tsList

    @classmethod
    def CreateSeedFile(cls, downPath: str, basePath: str, baseUri: str, content: str, seedName: str="download.seed"):
        # 1.检查种子内容是否合法
        tsList = cls._CheckM3U8File(basePath, baseUri, content)
        if len(tsList) <= 0:
            print(f"FileManager.CreateSeedFile CheckM3U8File Error")
            return False
        
        # 2. 确保下载文件一定存在
        PathManager.MakeDirsByPath(downPath)

        # 3. 写种子文件
        try:
            absSeed = PathManager.JoinPath(downPath, seedName)
            with open(absSeed, 'w') as f:     # 不存在则创建
                print(f"FileManager.CreateSeedFile [{basePath}]")
                print(f"FileManager.CreateSeedFile [{baseUri}]")
                f.write(basePath)
                f.write("\n")
                f.write(baseUri)
                f.write("\n")
                f.write(content)       # 可写入初始内容
            return True
        except Exception as ex:
            print(f"FileManager.CreateSeedFile except:{str(ex)}")
        # return cls.AddFileInfo(filePath, seedFile)
        return False

    @classmethod
    def _ParseSeedFile(cls, absSeed: str):
        basePath = ""
        baseUri = ""
        content = ""
        # absFile = SysSetting.GetAbsolutePath(seedFile)

        if not os.path.exists(absSeed):    # 检查文件是否存在
            print(f"文件 {absSeed} 不存在")
            return basePath, baseUri, content
        try:
            with open(absSeed, 'rb') as f:     # 不存在则创建
                basePath = f.readline().decode().strip()
                baseUri = f.readline().decode().strip()
                # print("basePath", basePath)
                # print("baseUri", baseUri)
                content = f.read().decode()
                # print("content", content)
        except Exception as ex:
            print(f"ParseSeedFile Error:{str(ex)}")
        return basePath, baseUri, content

# 拽拽写的代码
# # vfffffffffffnngnglgnkvvkjkmkgkgk lg jkf,gvklkmgkefklmmfnknfmfnfmnmfmffknj kmg[]
# # kjjbhgdvcmnnl;aaaaaaaaaaaaaaaaaaaaaaaaa8;9;8

# fileItem = FileItem(fileName="111", fileSize=111, modifyAt="222")
# # fileItem.fileName ="111"
# # fileItem.fileSize = "11122"
# # fileItem.modifyAt = "333"
# print(fileItem)