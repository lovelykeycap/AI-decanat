#include "DecanatPanel.h"
#include "DecanatClient.h"
#include "DecanatAvatar.h"
#include "Kismet/GameplayStatics.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/FileHelper.h"
#include "InputCoreTypes.h"
#include "Styling/CoreStyle.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"

static TSharedPtr<FJsonObject> ReadJson(const FString& Value)
{
    TSharedPtr<FJsonObject> Object;
    FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Value), Object);
    return Object;
}
static FString Json(const TSharedPtr<FJsonObject>& Value)
{
    FString Out;
    FJsonSerializer::Serialize(Value.ToSharedRef(), TJsonWriterFactory<>::Create(&Out));
    return Out;
}

void SDecanatPanel::Construct(const FArguments& Args)
{
    Client = Args._Client; World = Args._World;
    Client->OnEvent.AddRaw(this, &SDecanatPanel::Handle);
    ChildSlot [
        SAssignNew(Switcher, SWidgetSwitcher)
        + SWidgetSwitcher::Slot() [
            SNew(SOverlay)
            + SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Top).Padding(36) [
                SNew(STextBlock).Text(FText::FromString(TEXT("ДЕКАНАТ\nИнформационный помощник"))).Font(FCoreStyle::GetDefaultFontStyle("Bold", 26))
            ]
            + SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Bottom).Padding(36) [
                SNew(SBox).WidthOverride(570) [
                    SNew(SBorder).BorderBackgroundColor(FLinearColor(0.025f,0.035f,0.05f,0.94f)).Padding(24) [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight().Padding(0,0,0,14) [ SAssignNew(Status, STextBlock).Text(FText::FromString(TEXT("Подключение…"))).AutoWrapText(true) ]
                        + SVerticalBox::Slot().AutoHeight().Padding(0,0,0,12) [ SNew(SBox).MaxDesiredHeight(120) [ SNew(SScrollBox) + SScrollBox::Slot() [ SAssignNew(Transcript, STextBlock).AutoWrapText(true).ColorAndOpacity(FSlateColor(FLinearColor(0.65f,0.7f,0.75f))) ] ] ]
                        + SVerticalBox::Slot().AutoHeight().Padding(0,0,0,18) [ SNew(SBox).MaxDesiredHeight(300) [ SNew(SScrollBox) + SScrollBox::Slot() [ SAssignNew(Answer, STextBlock).AutoWrapText(true).Font(FCoreStyle::GetDefaultFontStyle("Regular", 19)) ] ] ]
                        + SVerticalBox::Slot().AutoHeight() [
                            SNew(SHorizontalBox)
                            + SHorizontalBox::Slot().AutoWidth().Padding(0,0,12,0) [
                                SNew(SButton).Text(FText::FromString(TEXT("Начать разговор")))
                                .IsEnabled_Lambda([this]() { return bRuntimeReady && bIdle && !bMaintenance && Client->bConnected; })
                                .OnClicked_Lambda([this]() { Client->Send(TEXT("start")); return FReply::Handled(); })
                            ]
                            + SHorizontalBox::Slot().AutoWidth().Padding(0,0,12,0) [
                                SNew(SButton).Text(FText::FromString(TEXT("Завершить"))).IsEnabled_Lambda([this]() { return bActive; })
                                .OnClicked_Lambda([this]() { Client->Send(TEXT("finish")); return FReply::Handled(); })
                            ]
                            + SHorizontalBox::Slot().AutoWidth() [
                                SNew(SButton).Text(FText::FromString(TEXT("Источники"))).IsEnabled_Lambda([this]() { return bActive; })
                                .OnClicked_Lambda([this]() { bSources=!bSources; if (bSources) Client->Send(TEXT("sources")); return FReply::Handled(); })
                            ]
                        ]
                        + SVerticalBox::Slot().AutoHeight().Padding(0,16,0,0) [
                            SNew(SBox).MaxDesiredHeight(260).Visibility_Lambda([this]() { return bSources ? EVisibility::Visible : EVisibility::Collapsed; }) [
                                SNew(SScrollBox) + SScrollBox::Slot() [ SAssignNew(Sources, STextBlock).AutoWrapText(true) ]
                            ]
                        ]
                    ]
                ]
            ]
        ]
        + SWidgetSwitcher::Slot() [
            SNew(SBorder).Padding(60).BorderBackgroundColor(FLinearColor(0.03f,0.04f,0.06f,1)) [
                SNew(SVerticalBox)
                + SVerticalBox::Slot().AutoHeight() [ SNew(STextBlock).Text(FText::FromString(TEXT("Вход сотрудника"))).Font(FCoreStyle::GetDefaultFontStyle("Bold",28)) ]
                + SVerticalBox::Slot().AutoHeight().Padding(0,24) [ SAssignNew(Password, SEditableTextBox).IsPassword(true).HintText(FText::FromString(TEXT("Пароль"))) ]
                + SVerticalBox::Slot().AutoHeight() [ SNew(SButton).Text(FText::FromString(TEXT("Войти"))).OnClicked(this, &SDecanatPanel::Login) ]
                + SVerticalBox::Slot().AutoHeight() [ SNew(SButton).Text(FText::FromString(TEXT("Назад"))).OnClicked_Lambda([this]() { Password->SetText(FText::GetEmpty()); Switcher->SetActiveWidgetIndex(0); return FReply::Handled(); }) ]
            ]
        ]
        + SWidgetSwitcher::Slot() [
            SNew(SBorder).Padding(40).BorderBackgroundColor(FLinearColor(0.03f,0.04f,0.06f,1)) [
                SNew(SScrollBox) + SScrollBox::Slot() [
                    SNew(SVerticalBox)
                    + SVerticalBox::Slot().AutoHeight().Padding(0,0,0,20) [ SNew(STextBlock).Text(FText::FromString(TEXT("Настройки сотрудника"))).Font(FCoreStyle::GetDefaultFontStyle("Bold",28)) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SCheckBox).IsChecked_Lambda([this](){return bApi ? ECheckBoxState::Checked : ECheckBoxState::Unchecked;}).OnCheckStateChanged_Lambda([this](ECheckBoxState V){bApi=V==ECheckBoxState::Checked;}) [ SNew(STextBlock).Text(FText::FromString(TEXT("Использовать API вместо Codex"))) ] ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SCheckBox).IsChecked_Lambda([this](){return bFallback ? ECheckBoxState::Checked : ECheckBoxState::Unchecked;}).OnCheckStateChanged_Lambda([this](ECheckBoxState V){bFallback=V==ECheckBoxState::Checked;}) [ SNew(STextBlock).Text(FText::FromString(TEXT("Разрешить автоматический переход на платный API"))) ] ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(ApiModel, SEditableTextBox).HintText(FText::FromString(TEXT("Модель API"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,12,0,6) [ SNew(STextBlock).Text(FText::FromString(TEXT("Аудиоустройства: пустое поле — выбор ОС. Точные названия доступны в диагностике. После изменения требуется перезапуск."))).AutoWrapText(true) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(InputDevice, SEditableTextBox).HintText(FText::FromString(TEXT("Микрофон"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(OutputDevice, SEditableTextBox).HintText(FText::FromString(TEXT("Динамики"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Сохранить настройки"))).OnClicked(this,&SDecanatPanel::SaveSettings) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Разрешить новую попытку подключения к сервису"))).OnClicked_Lambda([this]() { Client->Request(TEXT("POST"), TEXT("/v1/admin/provider-reset"), TEXT(""), [](int32,const FString&){}); return FReply::Handled(); }) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(ApiKey, SEditableTextBox).IsPassword(true).HintText(FText::FromString(TEXT("Новый API-ключ"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Сохранить ключ в хранилище ОС"))).OnClicked(this,&SDecanatPanel::SaveKey) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,20,0,6) [ SAssignNew(PackagePath, SEditableTextBox).HintText(FText::FromString(TEXT("Путь к подготовленному ZIP-пакету базы знаний"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Проверить и установить базу"))).OnClicked(this,&SDecanatPanel::Upload) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,16) [ SAssignNew(AdminStatus, STextBlock).AutoWrapText(true) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Показать последние разговоры"))).OnClicked(this,&SDecanatPanel::ReadJournal) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(JournalText, STextBlock).AutoWrapText(true) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,16,0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Диагностика компонентов и устройств"))).OnClicked(this,&SDecanatPanel::Diagnostics) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(DiagnosticsText, STextBlock).AutoWrapText(true) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,16,0,6) [ SAssignNew(NewPassword, SEditableTextBox).IsPassword(true).HintText(FText::FromString(TEXT("Новый пароль сотрудника: 10–256 символов"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SAssignNew(RepeatPassword, SEditableTextBox).IsPassword(true).HintText(FText::FromString(TEXT("Повторите новый пароль"))) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,6) [ SNew(SButton).Text(FText::FromString(TEXT("Сменить пароль и выйти"))).OnClicked(this,&SDecanatPanel::ChangePassword) ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0,24) [ SNew(SButton).Text(FText::FromString(TEXT("Выйти из настроек"))).OnClicked_Lambda([this]() { Client->Request(TEXT("POST"),TEXT("/v1/admin/logout"),TEXT(""),[](int32,const FString&){}); Client->AdminToken.Empty(); JournalText->SetText(FText::GetEmpty()); Switcher->SetActiveWidgetIndex(0); RefreshHealth(); return FReply::Handled(); }) ]
                ]
            ]
        ]
    ];
    RefreshHealth();
}

SDecanatPanel::~SDecanatPanel() { if (Client) Client->OnEvent.RemoveAll(this); }

FReply SDecanatPanel::OnKeyDown(const FGeometry&, const FKeyEvent& Key)
{
    if (Key.IsControlDown() && Key.IsAltDown() && Key.GetKey()==EKeys::A && bIdle) {
        Switcher->SetActiveWidgetIndex(1); return FReply::Handled();
    }
    return FReply::Unhandled();
}

void SDecanatPanel::RefreshHealth()
{
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("GET"),TEXT("/v1/health"),TEXT(""),[Weak](int32 Code,const FString& Body){
        if (auto Self=Weak.Pin()) {
            auto Data=ReadJson(Body);
            auto* Avatar=Self->World.IsValid() ? Cast<ADecanatAvatar>(UGameplayStatics::GetActorOfClass(Self->World.Get(),ADecanatAvatar::StaticClass())) : nullptr;
            Self->bRuntimeReady = Code==200 && Data && Data->GetBoolField(TEXT("knowledge_ready")) && Data->GetBoolField(TEXT("audio_device_ready")) && Avatar && Avatar->IsConfigured();
            Self->Status->SetText(FText::FromString(Self->bRuntimeReady ? TEXT("Нажмите «Начать разговор» и задайте вопрос") : TEXT("Приложение требует настройки сотрудником")));
        }
    });
}

void SDecanatPanel::Handle(const FString& Kind,const FString&,const TSharedPtr<FJsonObject>& Data)
{
    if (Kind==TEXT("state") || Kind==TEXT("snapshot")) {
        const FString State=Data->GetStringField(TEXT("state"));
        bIdle=State==TEXT("idle");
        if(Kind==TEXT("snapshot")) bMaintenance=Data->GetBoolField(TEXT("maintenance"));
        if(State==TEXT("standing_up")) bError=false;
        bActive=State!=TEXT("idle") && State!=TEXT("sitting_down");
        const FString Label=State==TEXT("idle") && !bError ? (bRuntimeReady ? TEXT("Нажмите «Начать разговор» и задайте вопрос") : TEXT("Приложение требует настройки сотрудником")) : State==TEXT("standing_up") ? TEXT("Начинаем разговор…") : State==TEXT("listening") ? TEXT("Слушаю вас") : State==TEXT("thinking") ? TEXT("Думаю…") : State==TEXT("speaking") ? TEXT("Можно перебить меня, если хотите уточнить") : State==TEXT("transcribing") ? TEXT("Распознаю вопрос…") : TEXT("");
        if (!Label.IsEmpty()) Status->SetText(FText::FromString(Label));
    } else if (Kind==TEXT("turn_started")) { Transcript->SetText(FText::GetEmpty()); Answer->SetText(FText::GetEmpty()); Sources->SetText(FText::GetEmpty()); bSources=false; }
    else if (Kind==TEXT("transcript")) { Transcript->SetText(FText::FromString(Data->GetStringField(TEXT("text")))); Answer->SetText(FText::GetEmpty()); Sources->SetText(FText::GetEmpty()); bSources=false; }
    else if (Kind==TEXT("answer")) Answer->SetText(FText::FromString(Data->GetObjectField(TEXT("answer"))->GetStringField(TEXT("answer_text"))));
    else if (Kind==TEXT("error") || Kind==TEXT("command_error")) { bError=true; Status->SetText(FText::FromString(Data->GetStringField(TEXT("message")))); }
    else if (Kind==TEXT("clarify")) Status->SetText(FText::FromString(Data->GetStringField(TEXT("text"))));
    else if (Kind==TEXT("cleared")) { Transcript->SetText(FText::GetEmpty()); Answer->SetText(FText::GetEmpty()); Sources->SetText(FText::GetEmpty()); bSources=false; }
    else if (Kind==TEXT("sources")) {
        FString Text;
        for (const auto& Item : Data->GetArrayField(TEXT("items"))) {
            auto Source=Item->AsObject();
            Text+=Source->GetStringField(TEXT("document_title"))+TEXT(" · ")+Source->GetStringField(TEXT("locator"))+TEXT("\n")+Source->GetStringField(TEXT("source_label"))+TEXT("\n")+Source->GetStringField(TEXT("text"))+TEXT("\n\n");
        }
        Sources->SetText(FText::FromString(Text.IsEmpty()?TEXT("Нет источников для этой реплики"):Text));
    } else if (Kind==TEXT("idle_warning")) {
        if(Data->GetBoolField(TEXT("visible"))) Status->SetText(FText::FromString(FString::Printf(TEXT("Разговор завершится через %d секунд. Заговорите, чтобы продолжить."),Data->GetIntegerField(TEXT("seconds")))));
        else if(bActive) Status->SetText(FText::FromString(TEXT("Слушаю вас")));
    }
    else if (Kind==TEXT("maintenance")) bMaintenance=Data->GetBoolField(TEXT("enabled"));
    else if (Kind==TEXT("connection_error")) { bRuntimeReady=false; bActive=false; Status->SetText(FText::FromString(TEXT("Нет соединения с приложением. Обратитесь к сотруднику"))); }
}

FReply SDecanatPanel::Login()
{
    auto Data=MakeShared<FJsonObject>(); Data->SetStringField(TEXT("password"),Password->GetText().ToString()); Password->SetText(FText::GetEmpty());
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("POST"),TEXT("/v1/admin/login"),Json(Data),[Weak](int32 Code,const FString& Body){
        if (auto Self=Weak.Pin()) {
            auto Value=ReadJson(Body);
            if (Code!=200 || !Value) { Self->Password->SetError(FText::FromString(TEXT("Вход не выполнен"))); return; }
            Self->Client->AdminToken=Value->GetStringField(TEXT("admin_token"));
            Self->Switcher->SetActiveWidgetIndex(2);
            Self->Client->Request(TEXT("GET"),TEXT("/v1/admin/settings"),TEXT(""),[Weak](int32 Status,const FString& Settings){
                if (auto Current=Weak.Pin()) if (Status==200) {
                    Current->Settings=ReadJson(Settings);
                    if (Current->Settings) {
                        Current->bApi=Current->Settings->GetStringField(TEXT("provider"))==TEXT("api");
                        Current->bFallback=Current->Settings->GetBoolField(TEXT("api_fallback"));
                        Current->ApiModel->SetText(FText::FromString(Current->Settings->GetStringField(TEXT("api_model"))));
                        Current->InputDevice->SetText(FText::FromString(Current->Settings->GetStringField(TEXT("input_device"))));
                        Current->OutputDevice->SetText(FText::FromString(Current->Settings->GetStringField(TEXT("output_device"))));
                    }
                }
            });
        }
    });
    return FReply::Handled();
}

FReply SDecanatPanel::SaveSettings()
{
    if (!Settings) return FReply::Handled();
    Settings->SetStringField(TEXT("provider"),bApi?TEXT("api"):TEXT("codex"));
    Settings->SetBoolField(TEXT("api_fallback"),bFallback);
    Settings->SetStringField(TEXT("api_model"),ApiModel->GetText().ToString());
    Settings->SetStringField(TEXT("input_device"),InputDevice->GetText().ToString());
    Settings->SetStringField(TEXT("output_device"),OutputDevice->GetText().ToString());
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("PUT"),TEXT("/v1/admin/settings"),Json(Settings),[Weak](int32 Code,const FString& Body){if(auto S=Weak.Pin()) {
        auto Result=ReadJson(Body);
        const bool Restart=Code==200 && Result && Result->GetBoolField(TEXT("restart_required"));
        S->AdminStatus->SetText(FText::FromString(Code==200 ? (Restart ? TEXT("Настройки сохранены. Перед новым разговором перезапустите приложение.") : TEXT("Настройки сохранены")) : TEXT("Не удалось сохранить настройки")));
        S->RefreshHealth();
    }});
    return FReply::Handled();
}

FReply SDecanatPanel::SaveKey()
{
    const FString Key=ApiKey->GetText().ToString(); ApiKey->SetText(FText::GetEmpty());
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("POST"),TEXT("/v1/admin/api-key"),Key,[Weak](int32 Code,const FString&){if(auto S=Weak.Pin()) S->AdminStatus->SetText(FText::FromString(Code==200?TEXT("Ключ сохранён"):TEXT("Не удалось сохранить ключ")));},TEXT("text/plain"));
    return FReply::Handled();
}

FReply SDecanatPanel::Upload()
{
    // ZIP is sent byte-for-byte, never through UTF-8 conversion.
    TArray<uint8> Data;
    if (!FFileHelper::LoadFileToArray(Data,*PackagePath->GetText().ToString()) || Data.Num()>50*1024*1024) { AdminStatus->SetText(FText::FromString(TEXT("Не удалось прочитать пакет (до 50 MiB)"))); return FReply::Handled(); }
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    // Binary helper is implemented in DecanatClient; no external file picker is exposed to students.
    Client->RequestBinary(TEXT("/v1/admin/knowledge"),Data,[Weak](int32 Code,const FString& Body){if(auto S=Weak.Pin()) {S->AdminStatus->SetText(FText::FromString(Code==200?TEXT("База проверена и установлена"):Body)); S->RefreshHealth();}});
    return FReply::Handled();
}

FReply SDecanatPanel::ReadJournal()
{
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("GET"),TEXT("/v1/admin/journal?limit=30"),TEXT(""),[Weak](int32 Code,const FString& Body){
        if(auto S=Weak.Pin()) {
            auto Data=ReadJson(Body); FString Text;
            if(Code==200 && Data) for(auto Item:Data->GetArrayField(TEXT("items"))) {
                auto Row=Item->AsObject();
                Text+=Row->GetStringField(TEXT("created_at"))+TEXT("\nВопрос: ")+Row->GetStringField(TEXT("question"))+TEXT("\nОтвет: ")+Row->GetStringField(TEXT("answer"))+TEXT("\n\n");
            }
            S->JournalText->SetText(FText::FromString(Text.IsEmpty()?TEXT("Нет записей или доступ истёк"):Text));
        }
    });
    return FReply::Handled();
}

FReply SDecanatPanel::Diagnostics()
{
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("GET"),TEXT("/v1/admin/diagnostics"),TEXT(""),[Weak](int32 Code,const FString& Body){
        if(auto Self=Weak.Pin()) {
            auto Data=ReadJson(Body);
            if(Code!=200 || !Data) { Self->DiagnosticsText->SetText(FText::FromString(TEXT("Диагностика недоступна. Проверьте вход сотрудника."))); return; }
            const auto Health=Data->GetObjectField(TEXT("health"));
            FString Text=Data->GetStringField(TEXT("platform"))+TEXT("\n");
            Text+=FString::Printf(TEXT("База: %s. Аудиопоток: %s. Codex найден: %s. API-ключ настроен: %s.\n"),
                Health->GetBoolField(TEXT("knowledge_ready")) ? TEXT("готова") : TEXT("требует настройки"),
                Health->GetBoolField(TEXT("audio_device_ready")) ? TEXT("готов") : TEXT("не готов"),
                Data->GetBoolField(TEXT("codex_found")) ? TEXT("да") : TEXT("нет"),
                Data->GetBoolField(TEXT("api_key_configured")) ? TEXT("да") : TEXT("нет"));
            if(Health->GetBoolField(TEXT("restart_required"))) Text+=TEXT("Приложение ожидает перезапуска после изменения аудионастроек.\n");
            Text+=Health->GetStringField(TEXT("knowledge_error"))+TEXT("\n")+Health->GetStringField(TEXT("audio_error"))+TEXT("\n");
            Text+=Data->GetObjectField(TEXT("audio"))->GetStringField(TEXT("message"))+TEXT("\n");
            for(const auto& Item:Data->GetObjectField(TEXT("audio"))->GetArrayField(TEXT("devices"))) {
                const auto Device=Item->AsObject();
                Text+=FString::Printf(TEXT("%s · входов %d, выходов %d\n"),*Device->GetStringField(TEXT("selection")),Device->GetIntegerField(TEXT("input_channels")),Device->GetIntegerField(TEXT("output_channels")));
            }
            Self->DiagnosticsText->SetText(FText::FromString(Text));
        }
    });
    return FReply::Handled();
}

FReply SDecanatPanel::ChangePassword()
{
    const FString Value=NewPassword->GetText().ToString();
    const bool Match=Value==RepeatPassword->GetText().ToString();
    NewPassword->SetText(FText::GetEmpty()); RepeatPassword->SetText(FText::GetEmpty());
    if(!Match || Value.Len()<10 || Value.Len()>256) { AdminStatus->SetText(FText::FromString(TEXT("Пароли должны совпадать и содержать от 10 до 256 символов"))); return FReply::Handled(); }
    auto Body=MakeShared<FJsonObject>(); Body->SetStringField(TEXT("password"),Value);
    TWeakPtr<SDecanatPanel> Weak=SharedThis(this);
    Client->Request(TEXT("POST"),TEXT("/v1/admin/password"),Json(Body),[Weak](int32 Code,const FString&){
        if(auto Self=Weak.Pin()) {
            if(Code!=200) { Self->AdminStatus->SetText(FText::FromString(TEXT("Не удалось сменить пароль"))); return; }
            Self->Client->AdminToken.Empty(); Self->Settings.Reset();
            Self->JournalText->SetText(FText::GetEmpty()); Self->DiagnosticsText->SetText(FText::GetEmpty());
            Self->Switcher->SetActiveWidgetIndex(1);
        }
    });
    return FReply::Handled();
}
