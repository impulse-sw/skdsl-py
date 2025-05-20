# This is the simple Server Kit DSL example.

For example, I want to create type definitions:

```
type HelloData         crate::api::types::HelloData
type AnswerData        crate::api::types::AnswerData
type UserChangePassReq crate::api::types::UserChangePasswordRequest
type ComplexAliasType  HashMap<String, u32>
```

So, now we must create our first API endpoints:

```
api tag users
api post/sign-in h/str/X-Sign b/json/HelloData q/i64/user_id                                        -> b/json/AnswerData
api patch/change-password h/str/X-Access h/str/X-Refresh h/str/X-Client b/msgpack/UserChangePassReq -> ok
```

And our first requirement common to all API endpoints with `chat` tag:

```
req tokens h/str/X-Access h/str/X-Refresh h/str/X-Client

type ChatData crate::api::types::ChatData

api tag chat req/tokens
api get/chats q/i64/chat_id                       -> b/json/Vec<ChatData>
api get/chat/{u64/id}                             -> b/json/ChatData
api post/chat/{u64/id}/audio-request b/file/audio -> ok
```

And something else:

```
req master h/str/X-Access h/str/X-Refresh h/str/X-Client -> h/str/X-Sign
req/hidden slave c/gitlab_session

api tag test
api req/master get/test                   -> ok c/X-Sign
api req/slave  post/audio f/Vec<u8>/audio -> b/msgpack/ComplexAliasType
```
