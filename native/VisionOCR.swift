import Foundation
import Vision
import ImageIO

// Output word coordinates in the exact same top-left pixel space as Tesseract.
do {
    guard CommandLine.arguments.count == 2,
          let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "VisionOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot read page image"])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false // Preserve names, UPI IDs and printed numbers.
    request.recognitionLanguages = ["en-US"]
    request.minimumTextHeight = 0.003
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    var words = [[String: Any]]()
    for observation in request.results ?? [] {
        let candidates=observation.topCandidates(3)
        guard let text = candidates.first else { continue }
        let value = text.string
        let regex = try NSRegularExpression(pattern: "\\S+")
        for match in regex.matches(in: value, range: NSRange(value.startIndex..., in: value)) {
            guard let range = Range(match.range, in: value),
                  let box = try text.boundingBox(for: range) else { continue }
            let rect = box.boundingBox
            words.append(["text": String(value[range]), "x": rect.minX * Double(image.width),
                          "y": (1 - rect.maxY) * Double(image.height),
                          "w": rect.width * Double(image.width), "h": rect.height * Double(image.height),
                          "conf": Double(text.confidence),
                          "alternatives":candidates.dropFirst().map { $0.string }])
        }
    }
    let data = try JSONSerialization.data(withJSONObject: words, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(1)
}
