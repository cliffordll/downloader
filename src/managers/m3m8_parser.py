import os
import m3u8
from m3u8 import M3U8, Segment, SegmentList

from src.schemas.segment_base import SegmentItem

class M3U8Parser(M3U8):
    '''
    base_path: 自定义的前缀文件
    m3u8_uri: m3u8 文件的绝对路径
    '''
    def __init__(self, content=None, base_path=None, m3u8_uri=None, strict=False, custom_tags_parser=None,):
        # 当 base_path="" 时，解析url有问题。需要修改为None
        prefix_path = None
        if base_path:
            prefix_path = base_path
        prefix_uri = None
        if m3u8_uri:
            prefix_uri = m3u8_uri.rsplit('/', 1)[0]
        super().__init__(content, base_path=prefix_path, base_uri=prefix_uri, strict=strict, custom_tags_parser=custom_tags_parser)

    def parse_media(self):
        ts_list = []
        for index, segment in enumerate(self.segments):
            tsName = segment.uri
            # if isAbs:
            #     tsName = tsName.replace(self.base_uri, "")
            #     # tsName = tsName.replace("/", "_")
            #     tsName = tsName.replace("/", os.sep)
            # else:
            #     tsName = tsName.rsplit("/", 1)[1] # 如果base_uri为空，则ts地址需要是绝对路径，不然找不到下载地址。URI地址必含 /
            if tsName.startswith("http") or tsName.startswith("https"):
                tsName = tsName.rsplit("/", 1)[1]
            elif tsName.startswith('/'):
                tsName = tsName[1:]
            tsName = tsName.replace('/', os.sep)

            # print("tsn", index, tsName)
            # print("uri", index, segment.uri)
            # print("abs", index, segment.absolute_uri)
            # print("")
            # print(segment.key.method)
            # print(segment.key.absolute_uri)
            # print(segment.key.iv)

            # if isAbs:
            #     ts_list.append((tsName, segment.uri, segment.absolute_uri))
            # else:
            #     ts_list.append((tsName, segment.uri,))

            tsItem = SegmentItem(name=tsName, uri=segment.uri, absUri=segment.absolute_uri)
            ts_list.append(tsItem)
        
        return ts_list

    # def ttype(self):
    #     if not not self.m3u8_obj.playlists:
    #         print("#########################3 master", len(self.m3u8_obj.playlists))
    #         return "master"
    #     elif not not self.m3u8_obj.segments:
    #         print("#########################3 media", len(self.m3u8_obj.segments))
    #         return "media"
    #     else:
    #         print("#########################3 unknown")
    #         return "unknown"

    # def parse_master(self):
    #     # print(m3u8_obj.playlists)
    #     for variant in self.m3u8_obj.playlists:
    #         quality = variant.stream_info
    #         # print(quality)
    #         print(variant.uri)

    #     ts_urls = [variant.uri for variant in self.m3u8_obj.playlists]
    #     return ts_urls

    # def parse_media(self):
    #     # for key in self.m3u8_obj.keys:
    #     #     print(key.uri)
    #     #     print(key.method)
    #     #     print(key.iv)

    #     # print(type(self.m3u8_obj.data), self.m3u8_obj.data)

    #     print("####################################################1# data")
    #     # for key, val in self.m3u8_obj.data.items():
    #     #     print(key, val)

    #     for segment in self.m3u8_obj.segments:
    #         print("\n")
    #         print(type(segment), segment.dumps(last_segment=None))
    #         print("KEY", segment.key)
    #         print("URI", segment.uri)

    #     print("###################################################2# data")

    #     # for segment in self.m3u8_obj.segments:
    #     #     # print(segment.uri)
    #     #     print(segment.uri)
    #     #     # print(segment.absolute_uri)
    #     #     # print(segment.dumps())
    #     #     print(segment.key)
        
    #     # 获取 .ts 文件 uri 列表
        # ts_urls = [segment.uri for segment in self.m3u8_obj.segments]
        # return ts_urls