#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import json
import logging
import time
import traceback
from uuid import uuid4
from agent.canvas import Canvas
from api.db import TenantPermission
from api.db.db_models import DB, CanvasTemplate, User, UserCanvas, API4Conversation
from api.db.services.api_service import API4ConversationService
from api.db.services.common_service import CommonService
from api.utils import get_uuid
from api.utils.api_utils import get_data_openai
import tiktoken
from peewee import fn


class CanvasTemplateService(CommonService):
    model = CanvasTemplate


class UserCanvasService(CommonService):
    model = UserCanvas

    @classmethod
    @DB.connection_context()
    def get_list(cls, tenant_id,
                 page_number, items_per_page, orderby, desc, id, title):
        agents = cls.model.select()
        if id:
            agents = agents.where(cls.model.id == id)
        if title:
            agents = agents.where(cls.model.title == title)
        agents = agents.where(cls.model.user_id == tenant_id)
        if desc:
            agents = agents.order_by(cls.model.getter_by(orderby).desc())
        else:
            agents = agents.order_by(cls.model.getter_by(orderby).asc())

        agents = agents.paginate(page_number, items_per_page)

        return list(agents.dicts())
   
    @classmethod
    @DB.connection_context()
    def get_by_tenant_id(cls, pid):
        try:
            
            fields = [
                cls.model.id,
                cls.model.avatar,
                cls.model.title,
                cls.model.dsl,
                cls.model.description,
                cls.model.permission,
                cls.model.update_time,
                cls.model.user_id,
                cls.model.create_time,
                cls.model.create_date,
                cls.model.update_date,
                User.nickname,
                User.avatar.alias('tenant_avatar'),
            ]
            agents = cls.model.select(*fields) \
            .join(User, on=(cls.model.user_id == User.id)) \
            .where(cls.model.id == pid)
            # obj = cls.model.query(id=pid)[0]
            return True, agents.dicts()[0]
        except Exception as e:
            logging.exception(e)
            return False, None
     
    @classmethod
    @DB.connection_context()
    def get_by_tenant_ids(cls, joined_tenant_ids, user_id,
                          page_number, items_per_page,
                          orderby, desc, keywords,
                          ):
        fields = [
            cls.model.id,
            cls.model.avatar,
            cls.model.title,
            cls.model.dsl,
            cls.model.description,
            cls.model.permission,
            User.nickname,
            User.avatar.alias('tenant_avatar'),
            cls.model.update_time
        ]
        if keywords:
            agents = cls.model.select(*fields).join(User, on=(cls.model.user_id == User.id)).where(
                ((cls.model.user_id.in_(joined_tenant_ids) & (cls.model.permission == 
                                                                TenantPermission.TEAM.value)) | (
                    cls.model.user_id == user_id)),
                (fn.LOWER(cls.model.title).contains(keywords.lower()))
            )
        else:
            agents = cls.model.select(*fields).join(User, on=(cls.model.user_id == User.id)).where(
                ((cls.model.user_id.in_(joined_tenant_ids) & (cls.model.permission == 
                                                                TenantPermission.TEAM.value)) | (
                    cls.model.user_id == user_id))
            )
        if desc:
            agents = agents.order_by(cls.model.getter_by(orderby).desc())
        else:
            agents = agents.order_by(cls.model.getter_by(orderby).asc())
        count = agents.count()
        agents = agents.paginate(page_number, items_per_page)
        return list(agents.dicts()), count


def completion(tenant_id, agent_id, session_id=None, **kwargs):
    query = kwargs.get("query", "")
    files = kwargs.get("files", [])
    inputs = kwargs.get("inputs", {})
    user_id = kwargs.get("user_id", "")

    if session_id:
        e, conv = API4ConversationService.get_by_id(session_id)
        assert e, "Session not found!"
        if not conv.message:
            conv.message = []
        if not isinstance(conv.dsl, str):
            conv.dsl = json.dumps(conv.dsl, ensure_ascii=False)
        canvas = Canvas(conv.dsl, tenant_id, agent_id)
    else:
        e, cvs = UserCanvasService.get_by_id(agent_id)
        assert e, "Agent not found."
        assert cvs.user_id == tenant_id, "You do not own the agent."
        if not isinstance(cvs.dsl, str):
            cvs.dsl = json.dumps(cvs.dsl, ensure_ascii=False)
        session_id=get_uuid()
        canvas = Canvas(cvs.dsl, tenant_id, agent_id)
        canvas.reset()
        conv = {
            "id": session_id,
            "dialog_id": cvs.id,
            "user_id": user_id,
            "message": [],
            "source": "agent",
            "dsl": cvs.dsl
        }
        API4ConversationService.save(**conv)
        conv = API4Conversation(**conv)

    message_id = str(uuid4())
    conv.message.append({
        "role": "user",
        "content": query,
        "id": message_id
    })
    txt = ""
    for ans in canvas.run(query=query, files=files, user_id=user_id, inputs=inputs):
        ans["session_id"] = session_id
        if ans["event"] == "message":
            txt += ans["data"]["content"]
        yield "data:" + json.dumps(ans, ensure_ascii=False) + "\n\n"

    conv.message.append({"role": "assistant", "content": txt, "created_at": time.time(), "id": message_id})
    conv.reference = canvas.get_reference()
    conv.errors = canvas.error
    API4ConversationService.append_message(conv.id, conv.to_dict())


def completionOpenAI(tenant_id, agent_id, question, session_id=None, stream=True, **kwargs):
    """Main function for OpenAI-compatible completions, structured similarly to the completion function."""
    tiktokenenc = tiktoken.get_encoding("cl100k_base")
    e, cvs = UserCanvasService.get_by_id(agent_id)
    
    if not e:
        yield get_data_openai(
            id=session_id,
            model=agent_id,
            content="**ERROR**: Agent not found."
        )
        return
    
    if cvs.user_id != tenant_id:
        yield get_data_openai(
            id=session_id,
            model=agent_id,
            content="**ERROR**: You do not own the agent"
        )
        return
    
    if not isinstance(cvs.dsl, str):
        cvs.dsl = json.dumps(cvs.dsl, ensure_ascii=False)
    
    canvas = Canvas(cvs.dsl, tenant_id)
    canvas.reset()
    message_id = str(uuid4())
    
    # Handle new session creation
    if not session_id:
        query = canvas.get_preset_param()
        if query:
            for ele in query:
                if not ele["optional"]:
                    if not kwargs.get(ele["key"]):
                        yield get_data_openai(
                            id=None,
                            model=agent_id,
                            content=f"`{ele['key']}` is required",
                            completion_tokens=len(tiktokenenc.encode(f"`{ele['key']}` is required")),
                            prompt_tokens=len(tiktokenenc.encode(question if question else ""))
                        )
                        return
                    ele["value"] = kwargs[ele["key"]]
                if ele["optional"]:
                    if kwargs.get(ele["key"]):
                        ele["value"] = kwargs[ele['key']]
                    else:
                        if "value" in ele:
                            ele.pop("value")
        
        cvs.dsl = json.loads(str(canvas))
        session_id = get_uuid()
        conv = {
            "id": session_id,
            "dialog_id": cvs.id,
            "user_id": kwargs.get("user_id", "") if isinstance(kwargs, dict) else "",
            "message": [{"role": "assistant", "content": canvas.get_prologue(), "created_at": time.time()}],
            "source": "agent",
            "dsl": cvs.dsl
        }
        canvas.messages.append({"role": "user", "content": question, "id": message_id})
        canvas.add_user_input(question)

        API4ConversationService.save(**conv)
        conv = API4Conversation(**conv)
        if not conv.message:
            conv.message = []
        conv.message.append({
            "role": "user",
            "content": question,
            "id": message_id
        })
        
        if not conv.reference:
            conv.reference = []
        conv.reference.append({"chunks": [], "doc_aggs": []})
            
    # Handle existing session
    else:
        e, conv = API4ConversationService.get_by_id(session_id)
        if not e:
            yield get_data_openai(
                id=session_id,
                model=agent_id,
                content="**ERROR**: Session not found!"
            )
            return
        
        canvas = Canvas(json.dumps(conv.dsl), tenant_id)
        canvas.messages.append({"role": "user", "content": question, "id": message_id})
        canvas.add_user_input(question)
        
        if not conv.message:
            conv.message = []
        conv.message.append({
            "role": "user",
            "content": question,
            "id": message_id
        })
        
        if not conv.reference:
            conv.reference = []
        conv.reference.append({"chunks": [], "doc_aggs": []})
    
    # Process request based on stream mode
    final_ans = {"reference": [], "content": ""}
    prompt_tokens = len(tiktokenenc.encode(str(question)))
    
    if stream:
        try:
            completion_tokens = 0
            for ans in canvas.run(stream=True, bypass_begin=True):
                if ans.get("running_status"):
                    completion_tokens += len(tiktokenenc.encode(ans.get("content", "")))
                    yield "data: " + json.dumps(
                        get_data_openai(
                            id=session_id,
                            model=agent_id,
                            content=ans["content"],
                            object="chat.completion.chunk",
                            completion_tokens=completion_tokens,
                            prompt_tokens=prompt_tokens
                        ),
                        ensure_ascii=False
                    ) + "\n\n"
                    continue
                
                for k in ans.keys():
                    final_ans[k] = ans[k]
                
                completion_tokens += len(tiktokenenc.encode(final_ans.get("content", "")))
                yield "data: " + json.dumps(
                    get_data_openai(
                        id=session_id,
                        model=agent_id,
                        content=final_ans["content"],
                        object="chat.completion.chunk",
                        finish_reason="stop",
                        completion_tokens=completion_tokens,
                        prompt_tokens=prompt_tokens
                    ),
                    ensure_ascii=False
                ) + "\n\n"
            
            # Update conversation
            canvas.messages.append({"role": "assistant", "content": final_ans["content"], "created_at": time.time(), "id": message_id})
            canvas.history.append(("assistant", final_ans["content"]))
            if final_ans.get("reference"):
                canvas.reference.append(final_ans["reference"])
            conv.dsl = json.loads(str(canvas))
            API4ConversationService.append_message(conv.id, conv.to_dict())
            
            yield "data: [DONE]\n\n"
            
        except Exception as e:
            traceback.print_exc()
            conv.dsl = json.loads(str(canvas))
            API4ConversationService.append_message(conv.id, conv.to_dict())
            yield "data: " + json.dumps(
                get_data_openai(
                    id=session_id,
                    model=agent_id,
                    content="**ERROR**: " + str(e),
                    finish_reason="stop",
                    completion_tokens=len(tiktokenenc.encode("**ERROR**: " + str(e))),
                    prompt_tokens=prompt_tokens
                ),
                ensure_ascii=False
            ) + "\n\n"
            yield "data: [DONE]\n\n"
    
    else:  # Non-streaming mode
        try:
            all_answer_content = ""
            for answer in canvas.run(stream=False, bypass_begin=True):
                if answer.get("running_status"):
                    continue
                
                final_ans["content"] = "\n".join(answer["content"]) if "content" in answer else ""
                final_ans["reference"] = answer.get("reference", [])
                all_answer_content += final_ans["content"]
            
            final_ans["content"] = all_answer_content
            
            # Update conversation
            canvas.messages.append({"role": "assistant", "content": final_ans["content"], "created_at": time.time(), "id": message_id})
            canvas.history.append(("assistant", final_ans["content"]))
            if final_ans.get("reference"):
                canvas.reference.append(final_ans["reference"])
            conv.dsl = json.loads(str(canvas))
            API4ConversationService.append_message(conv.id, conv.to_dict())
            
            # Return the response in OpenAI format
            yield get_data_openai(
                id=session_id,
                model=agent_id,
                content=final_ans["content"],
                finish_reason="stop",
                completion_tokens=len(tiktokenenc.encode(final_ans["content"])),
                prompt_tokens=prompt_tokens,
                param=canvas.get_preset_param()  # Added param info like in completion
            )
            
        except Exception as e:
            traceback.print_exc()
            conv.dsl = json.loads(str(canvas))
            API4ConversationService.append_message(conv.id, conv.to_dict())
            yield get_data_openai(
                id=session_id,
                model=agent_id,
                content="**ERROR**: " + str(e),
                finish_reason="stop",
                completion_tokens=len(tiktokenenc.encode("**ERROR**: " + str(e))),
                prompt_tokens=prompt_tokens
            )

