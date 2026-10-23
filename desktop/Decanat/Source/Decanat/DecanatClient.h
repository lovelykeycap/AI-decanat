#pragma once

#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"
#include "DecanatClient.generated.h"

class IWebSocket;
class FJsonObject;
DECLARE_MULTICAST_DELEGATE_ThreeParams(FDecanatEvent, const FString&, const FString&, const TSharedPtr<FJsonObject>&);

UCLASS()
class DECANAT_API UDecanatClient : public UGameInstanceSubsystem
{
    GENERATED_BODY()
public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
    void Send(const FString& Type, const FString& Text = TEXT(""));
    void Request(const FString& Method, const FString& Route, const FString& Body,
                 TFunction<void(int32, const FString&)> Complete, const FString& ContentType = TEXT("application/json"));
    void RequestBinary(const FString& Route, const TArray<uint8>& Bytes, TFunction<void(int32, const FString&)> Complete);
    FDecanatEvent OnEvent;
    FString SessionId, TurnId, AdminToken;
    bool bConnected = false;
private:
    TSharedPtr<IWebSocket> Socket;
    FString BaseUrl, Token;
    void Receive(const FString& Message);
};
