using UnrealBuildTool;
using System;
using System.IO;

public class Decanat : ModuleRules
{
    public Decanat(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] {"Core", "CoreUObject", "Engine", "InputCore"});
        PrivateDependencyModuleNames.AddRange(new string[] {"HTTP", "Json", "JsonUtilities", "WebSockets", "Slate", "SlateCore"});
        // Opt-in only after installing the verified NVIDIA plugin build. No cloud substitute.
        bool EnableACE = Target.Platform == UnrealTargetPlatform.Win64 && Environment.GetEnvironmentVariable("DECANAT_WITH_ACE") == "1";
        PublicDefinitions.Add("DECANAT_WITH_ACE=" + (EnableACE ? "1" : "0"));
        if (EnableACE) PrivateDependencyModuleNames.Add("ACERuntime");
    }
}
