$ErrorActionPreference = "Stop"

$sourcePath = "C:\Users\ASUS\Desktop\ForgeSight_产品特色_3.2_3.4_3.5_修改版.pptx"
$outputPath = "C:\Users\ASUS\Desktop\ForgeSight_产品特色_3.2_3.4_3.5_技术细节增强版.pptx"

Copy-Item -LiteralPath $sourcePath -Destination $outputPath -Force

function Get-Rgb([int]$r, [int]$g, [int]$b) {
    return $r + (256 * $g) + (65536 * $b)
}

$dark = Get-Rgb 26 40 52
$accentRed = Get-Rgb 229 57 53
$accentBlue = Get-Rgb 13 98 207
$fontName = "Microsoft YaHei"

$slides = @(
    @{
        Business = @(
            @{ Text = "• 主动发现：持续监测数据断流、良率下降和非计划停机。"; Prefix = "• 主动发现：" },
            @{ Text = "• 多端提醒：异常触发后通过站内消息、飞书或钉钉通知。"; Prefix = "• 多端提醒：" },
            @{ Text = "• 处置闭环：同步趋势、样本量、数据时间与恢复状态。"; Prefix = "• 处置闭环：" }
        )
        Technical = @(
            @{ Text = "规则建模：Rule Template 绑定数据源、表和字段，确定性验证后发布。"; Prefix = "规则建模：" },
            @{ Text = "周期调度：Monitoring Worker 按 cursor / watermark 读取完整周期。"; Prefix = "周期调度：" },
            @{ Text = "确定性评估：只读网关执行查询，LLM 不参与触发；PostgreSQL 保存状态与证据。"; Prefix = "确定性评估：" }
        )
        Summary = "确定性规则主动发现风险，让触发、持续与恢复全程可追溯。"
    },
    @{
        Business = @(
            @{ Text = "• 统一接入：在同一平台登记、扫描并治理多类数据库。"; Prefix = "• 统一接入：" },
            @{ Text = "• 原库分析：无需集中迁移，直接连接现有业务数据。"; Prefix = "• 原库分析：" },
            @{ Text = "• 快速扩展：新增数据库仅需实现统一适配器接口。"; Prefix = "• 快速扩展：" }
        )
        Technical = @(
            @{ Text = "适配器注册：Adapter Registry 统一 connect、scan、quote、execute 接口。"; Prefix = "适配器注册：" },
            @{ Text = "元数据归一：Schema、表、字段、主外键与注释映射统一资源模型。"; Prefix = "元数据归一：" },
            @{ Text = "安全网关：方言 SQL 经 AST 只读校验、白名单、限行、超时与 EXPLAIN 门控。"; Prefix = "安全网关：" }
        )
        Summary = "新增数据库只扩展适配器，无需重建 Agent 分析主流程。"
    },
    @{
        Business = @(
            @{ Text = "• Hermes 思路：把成功 Run、用户反馈沉淀为候选技能。"; Prefix = "• Hermes 思路：" },
            @{ Text = "• 审核复用：发布后的技能进入后续分析上下文。"; Prefix = "• 审核复用：" },
            @{ Text = "• 持续进化：新的使用反馈生成下一版本并留痕。"; Prefix = "• 持续进化：" }
        )
        Technical = @(
            @{ Text = "技能提取：从成功 Run 和 helpful / corrected 反馈提取特征、步骤与证据。"; Prefix = "技能提取：" },
            @{ Text = "治理状态：candidate → published / rejected / retired，版本与审核记录可追溯。"; Prefix = "治理状态：" },
            @{ Text = "混合检索：词法 + 语义召回并注入 Agent Context，反馈生成下一候选版本。"; Prefix = "混合检索：" }
        )
        Summary = "借鉴 Hermes 的经验学习机制，让高质量分析持续积累且不扩大数据权限。"
    }
)

$powerPoint = New-Object -ComObject PowerPoint.Application
$presentation = $null

try {
    $presentation = $powerPoint.Presentations.Open($outputPath, $false, $false, $false)

    for ($slideIndex = 1; $slideIndex -le $presentation.Slides.Count; $slideIndex++) {
        $slide = $presentation.Slides.Item($slideIndex)
        $content = $slides[$slideIndex - 1]

        # Remove the former prose lines while preserving section titles and numbered badges.
        for ($shapeIndex = $slide.Shapes.Count; $shapeIndex -ge 1; $shapeIndex--) {
            $shape = $slide.Shapes.Item($shapeIndex)
            if ($shape.HasTextFrame) {
                $left = [double]$shape.Left
                $top = [double]$shape.Top
                $isBusinessBody = ($left -ge 60 -and $left -lt 440 -and $top -ge 150 -and $top -lt 235)
                $isTechnicalBody = ($left -gt 85 -and $left -lt 440 -and $top -ge 285 -and $top -lt 440)
                if ($isBusinessBody -or $isTechnicalBody) {
                    $shape.Delete()
                }
            }
        }

        # Business service: compact, judge-facing bullets with emphasized lead phrases.
        $businessY = @(157.5, 180.0, 202.5)
        for ($i = 0; $i -lt 3; $i++) {
            $item = $content.Business[$i]
            $box = $slide.Shapes.AddTextbox(1, 63.8, $businessY[$i], 354.0, 20.5)
            $box.Name = "业务服务要点$($i + 1)"
            $box.TextFrame.MarginLeft = 0
            $box.TextFrame.MarginRight = 0
            $box.TextFrame.MarginTop = 0
            $box.TextFrame.MarginBottom = 0
            $box.TextFrame.WordWrap = -1
            $box.TextFrame.AutoSize = 0
            $box.TextFrame.TextRange.Text = $item.Text
            $box.TextFrame.TextRange.Font.Name = $fontName
            $box.TextFrame.TextRange.Font.NameFarEast = $fontName
            $box.TextFrame.TextRange.Font.Size = 12.5
            $box.TextFrame.TextRange.Font.Color.RGB = $dark
            $box.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 0
            $prefixRange = $box.TextFrame.TextRange.Characters(1, $item.Prefix.Length)
            $prefixRange.Font.Bold = -1
            $prefixRange.Font.Color.RGB = $accentRed
        }

        # Re-space the numbered badges to make room for two-line technical details.
        $badgeShapes = @()
        $numberShapes = @()
        for ($shapeIndex = 1; $shapeIndex -le $slide.Shapes.Count; $shapeIndex++) {
            $shape = $slide.Shapes.Item($shapeIndex)
            if ([math]::Abs([double]$shape.Left - 62.25) -lt 1.0 -and [math]::Abs([double]$shape.Width - 21.75) -lt 1.0 -and $shape.Top -ge 280 -and $shape.Top -lt 430) {
                $badgeShapes += $shape
            }
            if ($shape.HasTextFrame -and $shape.TextFrame.HasText -and $shape.Left -ge 60 -and $shape.Left -lt 85 -and $shape.Top -ge 280 -and $shape.Top -lt 430) {
                $numberText = $shape.TextFrame.TextRange.Text.Trim()
                if ($numberText -in @("1", "2", "3")) {
                    $numberShapes += $shape
                }
            }
        }
        $badgeShapes = $badgeShapes | Sort-Object Top
        $numberShapes = $numberShapes | Sort-Object Top
        $badgeY = @(290.5, 339.0, 387.5)
        $numberY = @(296.0, 344.5, 393.0)
        for ($i = 0; $i -lt 3; $i++) {
            $badgeShapes[$i].Top = $badgeY[$i]
            $numberShapes[$i].Top = $numberY[$i]
        }

        # Technical implementation: concrete components, contracts, state models and safety gates.
        $technicalY = @(289.0, 337.5, 386.0)
        for ($i = 0; $i -lt 3; $i++) {
            $item = $content.Technical[$i]
            $box = $slide.Shapes.AddTextbox(1, 92.5, $technicalY[$i], 323.0, 39.5)
            $box.Name = "底层实现技术点$($i + 1)"
            $box.TextFrame.MarginLeft = 0
            $box.TextFrame.MarginRight = 0
            $box.TextFrame.MarginTop = 0
            $box.TextFrame.MarginBottom = 0
            $box.TextFrame.WordWrap = -1
            $box.TextFrame.AutoSize = 0
            $box.TextFrame.TextRange.Text = $item.Text
            $box.TextFrame.TextRange.Font.Name = $fontName
            $box.TextFrame.TextRange.Font.NameFarEast = $fontName
            $box.TextFrame.TextRange.Font.Size = 11.4
            $box.TextFrame.TextRange.Font.Color.RGB = $dark
            $box.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 0
            $prefixRange = $box.TextFrame.TextRange.Characters(1, $item.Prefix.Length)
            $prefixRange.Font.Bold = -1
            $prefixRange.Font.Color.RGB = $accentBlue
        }

        # Replace the footer statement and use the available width across the slide.
        for ($shapeIndex = 1; $shapeIndex -le $slide.Shapes.Count; $shapeIndex++) {
            $shape = $slide.Shapes.Item($shapeIndex)
            if ($shape.HasTextFrame -and $shape.TextFrame.HasText -and $shape.Left -lt 500 -and $shape.Top -ge 455 -and $shape.Top -lt 500) {
                $shape.Left = 57.2
                $shape.Top = 463.5
                $shape.Width = 760.0
                $shape.Height = 24.0
                $shape.TextFrame.MarginLeft = 0
                $shape.TextFrame.MarginRight = 0
                $shape.TextFrame.MarginTop = 0
                $shape.TextFrame.MarginBottom = 0
                $shape.TextFrame.WordWrap = -1
                $shape.TextFrame.AutoSize = 0
                $shape.TextFrame.TextRange.Text = $content.Summary
                $shape.TextFrame.TextRange.Font.Name = $fontName
                $shape.TextFrame.TextRange.Font.NameFarEast = $fontName
                $shape.TextFrame.TextRange.Font.Size = 13.5
                $shape.TextFrame.TextRange.Font.Bold = -1
                $shape.TextFrame.TextRange.Font.Color.RGB = Get-Rgb 15 88 135
            }
        }
    }

    $presentation.Save()
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation)
    }
    $powerPoint.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint)
}

Write-Output $outputPath
