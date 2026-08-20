param(
    [Parameter(Mandatory = $true)]
    [string]$WorkbookPath
)

$ErrorActionPreference = 'Stop'
$resolvedPath = (Resolve-Path -LiteralPath $WorkbookPath).Path
$excel = $null
$workbook = $null

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $workbook = $excel.Workbooks.Open($resolvedPath, 0, $false)
    if ($workbook.ReadOnly) {
        throw 'Revenue Store workbook opened read-only and cannot be verified as saved.'
    }
    $excel.CalculateFullRebuild()
    $workbook.Save()
}
finally {
    if ($null -ne $workbook) {
        $workbook.Close($false)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook)
    }
    if ($null -ne $excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
