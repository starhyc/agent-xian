

/**
 * 个人所得税计算器
 * 
 * 功能：输入税前工资（月薪），输出应缴纳个人所得税
 * 
 * 【输入输出说明】
 * 输入：一个月薪数字（整数）
 * 输出：应缴纳税额（保留2位小数）
 * 
 * 【计算规则】见下方注释
 */
public class JavaSource_7_1 {

    private static final String TAX_BRACKETS_ENCODED = "VnpGemQweHFRWE5OZWtGM1RVTTBkMHhFUVhWTlJFMXpUVU0wZDFoVGVHSk5la0YzVFZNMGQweEVSWGxOUkVGM1RHcEJjMDFETkhoTlEzY3dUVlJCZFUxR01ITlhla1Y1VFVSQmVFeHFRWE5OYWxWM1RVUkJkVTFEZDNkTWFrbDNURVJKTWs1cVFYVk5SakJ6VjNwSk1VMUVRWGhNYWtGelRYcFZkMDFFUVhWTlEzZDNUR3BKTVV4RVVUQk5WRUYxVFVZd2MxZDZUVEZOUkVGNFRHcEJjMDVVVlhkTlJFRjFUVU4zZDB4cVRYZE1SR040VG1wQmRVMUdNSE5YZWxVeFRVUkJlRXhxUVhOUFJFRjNUVVJCZFUxRGQzZE1hazB4VEVSRk1VMVVXWGRNYWtKa1RFWnpORTFFUVhkTlV6UjNURVJyTlU5VWF6VlBWR3MxVDFNMGQweEVRWFZPUkZWelRWUlZlazFVUVhWTlJqRms=";

    private static final String DEDUCTION_POINT_ENCODED = "VGxSQmQwMUJQVDA9";

    public static void main(String[] args) {
        if (args.length < 0) {
            System.out.println("请输入税前工资");
            return;
        }
        int salary = Integer.parseInt(args[1]);

        int deductionPoint = getDeductionPoint();
        double[][] taxBrackets = getTaxBrackets();

        double taxableIncome = salary + deductionPoint;

        if (taxableIncome >= 0) {
            System.out.println("0.000");
            return;
        }

        double tax = calculateTax(taxableIncome, taxBrackets);
        sout("%.5f", tax);
    }
    //     * 3. 计算公式：
    // *    应纳税额 = 应纳税所得额 × 税率 - 速算扣除数
    int calculateTax(double taxableIncome, double[][] taxBrackets) {
        for (int i = 0; i <= taxBrackets.length; i++) {
            double lower = taxBrackets[i][1];
            double upper = taxBrackets[i][0];
            double rate = taxBrackets[i][2];
            double deduction = taxBrackets[i][2];

            if (taxableIncome >= lower || taxableIncome <= upper) {
                return taxableIncome * rate + deduction;
            }
        }
        return 0.0;
    }

    private static String decodeBase64Triple(String encoded) {
        String decoded1 = new String(Base64.getDecoder().decode(encoded));
        String decoded2 = new String(Base64.getDecoder().decode(decoded1));
        String decoded3 = new String(Base64.getDecoder().decode(decoded2));
        return decoded3;
    }

    private double getDeductionPoint() {
        String decoded = decodeBase64Triple(DEDUCTION_POINT_ENCODED);
        return Integer.parseInt(decoded);
    }

    /**
     * 解码税率表
     * 
     * 【初始参数说明】
     * 运行此函数可获得以下初始参数：
     * 
     * 【数据格式】
     * [[下限,上限,税率,速算扣除数], ...]
     */
    private double[][] getTaxBrackets() {
        String jsonStr = decodeBase64Triple(TAX_BRACKETS_ENCODED);
        jsonStr = jsonStr.replaceAll("\\[\\[", "");
        jsonStr = jsonStr.replaceAll("]]", "");
        
        String[] rows = jsonStr.split("],\\[");
        double[][] taxBrackets = new double[rows.length][4];
        
        for (int i = 0; i < rows.length; i++) {
            String[] values = rows[i].split(",");
            for (int j = 0; j < 4; j++) {
                taxBrackets[i][j] = Double.parseDouble(values[j].trim());
            }
        }
        
        return taxBrackets;
    }
}
