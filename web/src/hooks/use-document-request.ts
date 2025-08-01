import { useHandleFilterSubmit } from '@/components/list-filter-bar/use-handle-filter-submit';
import {
  IDocumentInfo,
  IDocumentInfoFilter,
} from '@/interfaces/database/document';
import {
  IChangeParserConfigRequestBody,
  IDocumentMetaRequestBody,
} from '@/interfaces/request/document';
import i18n from '@/locales/config';
import kbService, { listDocument } from '@/services/knowledge-service';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDebounce } from 'ahooks';
import { message } from 'antd';
import { get } from 'lodash';
import { useCallback, useMemo, useState } from 'react';
import { useParams } from 'umi';
import {
  useGetPaginationWithRouter,
  useHandleSearchChange,
} from './logic-hooks';
import {
  useGetKnowledgeSearchParams,
  useSetPaginationParams,
} from './route-hook';

export const enum DocumentApiAction {
  UploadDocument = 'uploadDocument',
  FetchDocumentList = 'fetchDocumentList',
  UpdateDocumentStatus = 'updateDocumentStatus',
  RunDocumentByIds = 'runDocumentByIds',
  RemoveDocument = 'removeDocument',
  SaveDocumentName = 'saveDocumentName',
  SetDocumentParser = 'setDocumentParser',
  SetDocumentMeta = 'setDocumentMeta',
  FetchDocumentFilter = 'fetchDocumentFilter',
  CreateDocument = 'createDocument',
}

export const useUploadNextDocument = () => {
  const queryClient = useQueryClient();
  const { id } = useParams();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.UploadDocument],
    mutationFn: async (fileList: File[]) => {
      const formData = new FormData();
      formData.append('kb_id', id!);
      fileList.forEach((file: any) => {
        formData.append('file', file);
      });

      try {
        const ret = await kbService.document_upload(formData);
        const code = get(ret, 'data.code');

        if (code === 0 || code === 500) {
          queryClient.invalidateQueries({
            queryKey: [DocumentApiAction.FetchDocumentList],
          });
        }
        return ret?.data;
      } catch (error) {
        console.warn(error);
        return {
          code: 500,
          message: error + '',
        };
      }
    },
  });

  return { uploadDocument: mutateAsync, loading, data };
};

export const useFetchDocumentList = () => {
  const { knowledgeId } = useGetKnowledgeSearchParams();
  const { searchString, handleInputChange } = useHandleSearchChange();
  const { pagination, setPagination } = useGetPaginationWithRouter();
  const { id } = useParams();
  const debouncedSearchString = useDebounce(searchString, { wait: 500 });
  const { filterValue, handleFilterSubmit } = useHandleFilterSubmit();
  const [docs, setDocs] = useState<IDocumentInfo[]>([]);
  const isLoop = useMemo(() => {
    return docs.some((doc) => doc.run === '1');
  }, [docs]);

  const { data, isFetching: loading } = useQuery<{
    docs: IDocumentInfo[];
    total: number;
  }>({
    queryKey: [
      DocumentApiAction.FetchDocumentList,
      debouncedSearchString,
      pagination,
      filterValue,
    ],
    initialData: { docs: [], total: 0 },
    refetchInterval: isLoop ? 5000 : false,
    enabled: !!knowledgeId || !!id,
    queryFn: async () => {
      const ret = await listDocument(
        {
          kb_id: knowledgeId || id,
          keywords: debouncedSearchString,
          page_size: pagination.pageSize,
          page: pagination.current,
        },
        {
          suffix: filterValue.type,
          run_status: filterValue.run,
        },
      );
      if (ret.data.code === 0) {
        return ret.data.data;
      }

      return {
        docs: [],
        total: 0,
      };
    },
  });
  useMemo(() => {
    setDocs(data.docs);
  }, [data.docs]);
  const onInputChange: React.ChangeEventHandler<HTMLInputElement> = useCallback(
    (e) => {
      setPagination({ page: 1 });
      handleInputChange(e);
    },
    [handleInputChange, setPagination],
  );

  return {
    loading,
    searchString,
    documents: data.docs,
    pagination: { ...pagination, total: data?.total },
    handleInputChange: onInputChange,
    setPagination,
    filterValue,
    handleFilterSubmit,
  };
};

// get document filter
export const useGetDocumentFilter = (): {
  filter: IDocumentInfoFilter;
  onOpenChange: (open: boolean) => void;
} => {
  const { knowledgeId } = useGetKnowledgeSearchParams();
  const { searchString } = useHandleSearchChange();
  const { id } = useParams();
  const debouncedSearchString = useDebounce(searchString, { wait: 500 });
  const [open, setOpen] = useState<number>(0);
  const { data } = useQuery({
    queryKey: [
      DocumentApiAction.FetchDocumentFilter,
      debouncedSearchString,
      knowledgeId,
      open,
    ],
    queryFn: async () => {
      const { data } = await kbService.documentFilter({
        kb_id: knowledgeId || id,
        keywords: debouncedSearchString,
      });
      if (data.code === 0) {
        return data.data;
      }
    },
  });
  const handleOnpenChange = (e: boolean) => {
    if (e) {
      const currentOpen = open + 1;
      setOpen(currentOpen);
    }
  };
  return {
    filter: data?.filter || {
      run_status: {},
      suffix: {},
    },
    onOpenChange: handleOnpenChange,
  };
};
// update document status
export const useSetDocumentStatus = () => {
  const queryClient = useQueryClient();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.UpdateDocumentStatus],
    mutationFn: async ({
      status,
      documentId,
    }: {
      status: boolean;
      documentId: string | string[];
    }) => {
      const ids = Array.isArray(documentId) ? documentId : [documentId];
      const { data } = await kbService.document_change_status({
        doc_ids: ids,
        status: Number(status),
      });
      if (data.code === 0) {
        message.success(i18n.t('message.modified'));
        queryClient.invalidateQueries({
          queryKey: [DocumentApiAction.FetchDocumentList],
        });
      }
      return data;
    },
  });

  return { setDocumentStatus: mutateAsync, data, loading };
};

// This hook is used to run a document by its IDs
export const useRunDocument = () => {
  const queryClient = useQueryClient();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.RunDocumentByIds],
    mutationFn: async ({
      documentIds,
      run,
      shouldDelete,
    }: {
      documentIds: string[];
      run: number;
      shouldDelete: boolean;
    }) => {
      queryClient.invalidateQueries({
        queryKey: [DocumentApiAction.FetchDocumentList],
      });

      const ret = await kbService.document_run({
        doc_ids: documentIds,
        run,
        delete: shouldDelete,
      });
      const code = get(ret, 'data.code');
      if (code === 0) {
        queryClient.invalidateQueries({
          queryKey: [DocumentApiAction.FetchDocumentList],
        });
        message.success(i18n.t('message.operated'));
      }

      return code;
    },
  });

  return { runDocumentByIds: mutateAsync, loading, data };
};

export const useRemoveDocument = () => {
  const queryClient = useQueryClient();
  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.RemoveDocument],
    mutationFn: async (documentIds: string | string[]) => {
      const { data } = await kbService.document_rm({ doc_id: documentIds });
      if (data.code === 0) {
        message.success(i18n.t('message.deleted'));
        queryClient.invalidateQueries({
          queryKey: [DocumentApiAction.FetchDocumentList],
        });
      }
      return data.code;
    },
  });

  return { data, loading, removeDocument: mutateAsync };
};

export const useSaveDocumentName = () => {
  const queryClient = useQueryClient();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.SaveDocumentName],
    mutationFn: async ({
      name,
      documentId,
    }: {
      name: string;
      documentId: string;
    }) => {
      const { data } = await kbService.document_rename({
        doc_id: documentId,
        name: name,
      });
      if (data.code === 0) {
        message.success(i18n.t('message.renamed'));
        queryClient.invalidateQueries({
          queryKey: [DocumentApiAction.FetchDocumentList],
        });
      }
      return data.code;
    },
  });

  return { loading, saveName: mutateAsync, data };
};

export const useSetDocumentParser = () => {
  const queryClient = useQueryClient();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.SetDocumentParser],
    mutationFn: async ({
      parserId,
      documentId,
      parserConfig,
    }: {
      parserId: string;
      documentId: string;
      parserConfig: IChangeParserConfigRequestBody;
    }) => {
      const { data } = await kbService.document_change_parser({
        parser_id: parserId,
        doc_id: documentId,
        parser_config: parserConfig,
      });
      if (data.code === 0) {
        queryClient.invalidateQueries({
          queryKey: [DocumentApiAction.FetchDocumentList],
        });

        message.success(i18n.t('message.modified'));
      }
      return data.code;
    },
  });

  return { setDocumentParser: mutateAsync, data, loading };
};

export const useSetDocumentMeta = () => {
  const queryClient = useQueryClient();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.SetDocumentMeta],
    mutationFn: async (params: IDocumentMetaRequestBody) => {
      try {
        const { data } = await kbService.setMeta({
          meta: params.meta,
          doc_id: params.documentId,
        });

        if (data?.code === 0) {
          queryClient.invalidateQueries({
            queryKey: [DocumentApiAction.FetchDocumentList],
          });

          message.success(i18n.t('message.modified'));
        }
        return data?.code;
      } catch (error) {
        message.error('error');
      }
    },
  });

  return { setDocumentMeta: mutateAsync, data, loading };
};

export const useCreateDocument = () => {
  const { id } = useParams();
  const { setPaginationParams, page } = useSetPaginationParams();
  const queryClient = useQueryClient();

  const {
    data,
    isPending: loading,
    mutateAsync,
  } = useMutation({
    mutationKey: [DocumentApiAction.CreateDocument],
    mutationFn: async (name: string) => {
      const { data } = await kbService.document_create({
        name,
        kb_id: id,
      });
      if (data.code === 0) {
        if (page === 1) {
          queryClient.invalidateQueries({
            queryKey: [DocumentApiAction.FetchDocumentList],
          });
        } else {
          setPaginationParams(); // fetch document list
        }

        message.success(i18n.t('message.created'));
      }
      return data.code;
    },
  });

  return { createDocument: mutateAsync, loading, data };
};
