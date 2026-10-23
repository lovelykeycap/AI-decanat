#include "DecanatClient.h"
#include "IWebSocket.h"
#include "WebSocketsModule.h"
#include "HttpModule.h"
#include "Interfaces/IHttpResponse.h"
#include "Interfaces/IHttpRequest.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/PlatformMisc.h"
#include "Internationalization/Regex.h"

void UDecanatClient::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);
    FString Filename = FPlatformMisc::GetEnvironmentVariable(TEXT("DECANAT_CONNECTION_FILE"));
    if (Filename.IsEmpty()) return;
    FString Data;
    TSharedPtr<FJsonObject> Connection;
    if (!FFileHelper::LoadFileToString(Data, *Filename) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Data), Connection)) return;
    const FString CandidateUrl = Connection->GetStringField(TEXT("url"));
    const FString CandidateToken = Connection->GetStringField(TEXT("token"));
    // Do not send the IPC bearer to a remote host even if a connection file was edited.
    FRegexMatcher Address(FRegexPattern(TEXT("^http://127\\.0\\.0\\.1:([0-9]{1,5})$")), CandidateUrl);
    if (!Address.FindNext() || CandidateToken.Len() < 32) return;
    const int32 Port = FCString::Atoi(*Address.GetCaptureGroup(1));
    if (Port < 1 || Port > 65535) return;
    BaseUrl = CandidateUrl;
    Token = CandidateToken;
    FString WebSocketUrl = BaseUrl.Replace(TEXT("http://"), TEXT("ws://")) + TEXT("/v1/conversation");
    TMap<FString, FString> Headers;
    Headers.Add(TEXT("Authorization"), TEXT("Bearer ") + Token);
    Socket = FWebSocketsModule::Get().CreateWebSocket(WebSocketUrl, TEXT(""), Headers);
    Socket->OnConnected().AddLambda([this]() { bConnected = true; });
    Socket->OnConnectionError().AddLambda([this](const FString&) {
        bConnected = false;
        OnEvent.Broadcast(TEXT("connection_error"), TEXT(""), MakeShared<FJsonObject>());
    });
    Socket->OnClosed().AddLambda([this](int32, const FString&, bool) {
        bConnected = false;
        SessionId.Empty(); TurnId.Empty(); AdminToken.Empty();
        OnEvent.Broadcast(TEXT("connection_error"), TEXT(""), MakeShared<FJsonObject>());
    });
    Socket->OnMessage().AddUObject(this, &UDecanatClient::Receive);
    Socket->Connect();
}

void UDecanatClient::Deinitialize()
{
    if (Socket) {
        Socket->OnConnected().Clear(); Socket->OnConnectionError().Clear(); Socket->OnClosed().Clear();
        Socket->OnMessage().RemoveAll(this); Socket->Close(); Socket.Reset();
    }
    Token.Empty(); AdminToken.Empty();
    Super::Deinitialize();
}

void UDecanatClient::Receive(const FString& Message)
{
    TSharedPtr<FJsonObject> Event;
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Message), Event) || !Event) return;
    FString Kind;
    if (!Event->TryGetStringField(TEXT("kind"), Kind)) return;
    FString Id;
    if (Event->TryGetStringField(TEXT("session_id"), Id)) SessionId = Id;
    if (Event->TryGetStringField(TEXT("turn_id"), Id)) TurnId = Id;
    const TSharedPtr<FJsonObject>* Payload = nullptr;
    if (Event->TryGetObjectField(TEXT("payload"), Payload)) OnEvent.Broadcast(Kind, TurnId, *Payload);
    if (Kind == TEXT("state") && Payload && (*Payload)->GetStringField(TEXT("state")) == TEXT("idle")) {
        SessionId.Empty(); TurnId.Empty();
    }
}

void UDecanatClient::Send(const FString& Type, const FString& Text)
{
    if (!Socket || !Socket->IsConnected()) return;
    TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
    Data->SetStringField(TEXT("type"), Type);
    if (!SessionId.IsEmpty()) Data->SetStringField(TEXT("session_id"), SessionId);
    if (!TurnId.IsEmpty()) Data->SetStringField(TEXT("turn_id"), TurnId);
    if (!Text.IsEmpty()) Data->SetStringField(TEXT("text"), Text);
    FString Message;
    FJsonSerializer::Serialize(Data.ToSharedRef(), TJsonWriterFactory<>::Create(&Message));
    Socket->Send(Message);
}

void UDecanatClient::Request(const FString& Method, const FString& Route, const FString& Body,
                            TFunction<void(int32, const FString&)> Complete, const FString& ContentType)
{
    if (BaseUrl.IsEmpty()) { Complete(0, TEXT("Нет подключения к локальному приложению")); return; }
    TSharedRef<IHttpRequest, ESPMode::ThreadSafe> Request = FHttpModule::Get().CreateRequest();
    Request->SetURL(BaseUrl + Route);
    Request->SetVerb(Method);
    Request->SetHeader(TEXT("Authorization"), TEXT("Bearer ") + Token);
    Request->SetHeader(TEXT("Content-Type"), ContentType);
    if (!AdminToken.IsEmpty()) Request->SetHeader(TEXT("X-Admin-Token"), AdminToken);
    Request->SetContentAsString(Body);
    Request->OnProcessRequestComplete().BindLambda([Complete](FHttpRequestPtr, FHttpResponsePtr Response, bool Success) {
        Complete(Success && Response ? Response->GetResponseCode() : 0, Response ? Response->GetContentAsString() : TEXT("Нет соединения"));
    });
    Request->ProcessRequest();
}

void UDecanatClient::RequestBinary(const FString& Route, const TArray<uint8>& Bytes, TFunction<void(int32, const FString&)> Complete)
{
    if (BaseUrl.IsEmpty()) { Complete(0, TEXT("Нет подключения")); return; }
    auto Request = FHttpModule::Get().CreateRequest();
    Request->SetURL(BaseUrl + Route);
    Request->SetVerb(TEXT("POST"));
    Request->SetHeader(TEXT("Authorization"), TEXT("Bearer ") + Token);
    Request->SetHeader(TEXT("X-Admin-Token"), AdminToken);
    Request->SetHeader(TEXT("Content-Type"), TEXT("application/zip"));
    Request->SetContent(Bytes);
    Request->OnProcessRequestComplete().BindLambda([Complete](FHttpRequestPtr, FHttpResponsePtr Response, bool Success) {
        Complete(Success && Response ? Response->GetResponseCode() : 0, Response ? Response->GetContentAsString() : TEXT("Нет соединения"));
    });
    Request->ProcessRequest();
}
